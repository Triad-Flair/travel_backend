import json
import logging
import re
import uuid
from datetime import UTC, datetime
from collections.abc import Sequence

from slugify import slugify
from sqlalchemy import Text, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.core.cache import CacheKeys, TTL_MEDIUM, get_cached, invalidate, set_cached
from app.exceptions import BadRequestError, ForbiddenError, NotFoundError, PaymentError
from app.lib.gst import verify_gstin
from app.lib.ifsc import lookup_ifsc as lookup_ifsc_code
from app.lib.razorpay_route import configure_route_settlement, create_linked_account
from app.models.agency import Agency, AgencyBankAccount, AgencyMember, AgencyWallet
from app.models.social import Review
from app.models.user import User
from app.schemas.agencies import (
    AgencyProfile,
    CreateAgencyRequest,
    GstVerifyResponse,
    IfscLookupResponse,
    UpdateAgencyRequest,
)
from app.schemas.common import AgencyPublicSummary, AgencySummary

logger = logging.getLogger(__name__)


def _parse_json_list(raw: object) -> list | None:
    if raw is None:
        return None
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        return [v for v in raw]
    if isinstance(raw, bytes):
        raw = raw.decode(errors="ignore")
    if isinstance(raw, str):
        if not raw.strip():
            return None
        if not raw.strip().startswith("["):
            values = [part.strip() for part in raw.split(",") if part.strip()]
            return values or None
    try:
        val = json.loads(str(raw))
        return val if isinstance(val, list) else None
    except Exception:
        return None


def _agency_to_summary(agency: Agency) -> AgencySummary:
    """Full summary including GST/PAN — only for authenticated owner/member
    contexts (submit_verification, or the owner branch of get_agency_by_slug).
    Never return this from a public/unauthenticated route."""
    return AgencySummary(
        id=agency.id,
        name=agency.name,
        slug=agency.slug,
        logo_url=agency.logo_url,
        description=agency.description,
        verification=agency.verification_status,
        verification_rejection_reason=agency.verification_rejection_reason,
        gstin=agency.gstin,
        pan=agency.pan,
        tourism_license=agency.tourism_license,
        address=agency.address,
        phone=agency.phone,
        email=agency.email,
        city=agency.city,
        state=agency.state,
        specializations=_parse_json_list(agency.specializations),
        destinations=_parse_json_list(agency.destinations),
        avg_rating=agency.avg_rating,
        total_reviews=agency.review_count,
        total_trips=agency.total_trips,
    )


def _agency_to_public_summary(agency: Agency) -> AgencyPublicSummary:
    """GST/PAN-free summary for public/unauthenticated routes (browse, and the
    non-owner branch of get_agency_by_slug)."""
    return AgencyPublicSummary(
        id=agency.id,
        name=agency.name,
        slug=agency.slug,
        logo_url=agency.logo_url,
        description=agency.description,
        verification=agency.verification_status,
        tourism_license=agency.tourism_license,
        address=agency.address,
        phone=agency.phone,
        email=agency.email,
        city=agency.city,
        state=agency.state,
        specializations=_parse_json_list(agency.specializations),
        destinations=_parse_json_list(agency.destinations),
        avg_rating=agency.avg_rating,
        total_reviews=agency.review_count,
        total_trips=agency.total_trips,
    )


def _to_profile(agency: Agency) -> AgencyProfile:
    return AgencyProfile(
        id=agency.id,
        name=agency.name,
        slug=agency.slug,
        description=agency.description,
        logo_url=agency.logo_url,
        phone=agency.phone,
        email=agency.email,
        city=agency.city,
        state=agency.state,
        address=agency.address,
        postal_code=agency.postal_code,
        gstin=agency.gstin,
        pan=agency.pan,
        tourism_license=agency.tourism_license,
        specializations=_parse_json_list(agency.specializations),
        destinations=_parse_json_list(agency.destinations),
        verification_status=agency.verification_status,
        gstin_verified=bool(getattr(agency, "gst_verified_at", None)),
        avg_rating=agency.avg_rating,
        review_count=agency.review_count,
        total_trips=agency.total_trips,
        created_at=agency.created_at.isoformat() if agency.created_at else "",
    )


async def browse_agencies(
    db: AsyncSession,
    city: str | None,
    state: str | None,
    specialization: str | None,
    destination: str | None,
    page: int,
    page_size: int,
) -> tuple[list[AgencyPublicSummary], int]:
    q = select(Agency).where(Agency.is_active == True)
    if city:
        q = q.where(Agency.city.ilike(f"%{city}%"))
    if state:
        q = q.where(Agency.state.ilike(f"%{state}%"))
    if specialization:
        q = q.where(cast(Agency.specializations, Text).ilike(f"%{specialization}%"))
    if destination:
        q = q.where(cast(Agency.destinations, Text).ilike(f"%{destination}%"))

    total = await db.scalar(select(func.count(Agency.id)).where(Agency.is_active == True)) or 0
    result = await db.execute(
        q.order_by(Agency.avg_rating.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return [_agency_to_public_summary(a) for a in result.scalars().all()], total


async def _requester_has_agency_access(db: AsyncSession, agency: Agency, requesting_user_id: str) -> bool:
    if agency.owner_id == requesting_user_id:
        return True
    member = await db.scalar(
        select(AgencyMember).where(
            AgencyMember.agency_id == agency.id,
            AgencyMember.user_id == requesting_user_id,
            AgencyMember.is_active == True,
        )
    )
    return member is not None


async def get_agency_by_slug(db: AsyncSession, slug: str, requesting_user_id: str | None = None) -> dict:
    """Returns packages + reviewsReceived, plus either the full AgencySummary
    (GST/PAN included) if the requester owns/belongs to this agency, or the
    GST/PAN-free AgencyPublicSummary for everyone else. The public variant is
    the only one cached — the owner variant is per-user and computed fresh."""
    cache_key = CacheKeys.agency_by_slug(slug)
    if not requesting_user_id:
        cached_val = await get_cached(cache_key)
        if cached_val:
            return cached_val

    result = await db.execute(
        select(Agency).where(Agency.slug == slug, Agency.is_active == True)
    )
    agency = result.scalar_one_or_none()
    if not agency:
        raise NotFoundError("Agency")

    is_owner_context = bool(
        requesting_user_id and await _requester_has_agency_access(db, agency, requesting_user_id)
    )

    from app.models.package import Package
    from app.services.packages import _pkg_to_details
    pkg_result = await db.execute(
        select(Package)
        .options(selectinload(Package.agency))
        .where(Package.agency_id == agency.id, Package.status == "OPEN")
        .order_by(Package.created_at.desc())
        .limit(20)
    )
    packages = [_pkg_to_details(p).model_dump(by_alias=True) for p in pkg_result.scalars().all()]

    review_result = await db.execute(
        select(Review)
        .options(selectinload(Review.reviewer))
        .where(Review.target_agency_id == agency.id, Review.review_type == "agency")
        .order_by(Review.created_at.desc())
        .limit(20)
    )
    reviews = []
    for r in review_result.scalars().all():
        reviews.append({
            "id": r.id,
            "overallRating": r.overall_rating,
            "comment": r.comment,
            "createdAt": r.created_at.isoformat(),
            "reviewer": {
                "id": r.reviewer.id,
                "fullName": r.reviewer.display_name or r.reviewer.username or "",
                "avatarUrl": r.reviewer.avatar_url,
            } if r.reviewer else None,
        })

    summary = _agency_to_summary(agency) if is_owner_context else _agency_to_public_summary(agency)
    data = {
        **summary.model_dump(by_alias=True),
        "packages": packages,
        "reviewsReceived": reviews,
    }
    if not requesting_user_id:
        await set_cached(cache_key, data, TTL_MEDIUM)
    return data


async def create_agency(db: AsyncSession, owner_id: str, req: CreateAgencyRequest) -> AgencyProfile:
    slug = slugify(req.name) + "-" + str(uuid.uuid4())[:8]
    agency = Agency(
        id=str(uuid.uuid4()),
        owner_id=owner_id,
        name=req.name,
        slug=slug,
        description=req.description,
        city=req.city,
        state=req.state,
        address=req.address,
        postal_code=req.postal_code,
        phone=req.phone,
        email=req.email,
        gstin=req.gstin,
        pan=req.pan,
        tourism_license=req.tourism_license,
        specializations=req.specializations or None,
        destinations=req.destinations or None,
    )
    db.add(agency)
    await db.flush()
    db.add(AgencyMember(id=str(uuid.uuid4()), agency_id=agency.id, user_id=owner_id, role="ADMIN"))
    db.add(AgencyWallet(id=str(uuid.uuid4()), agency_id=agency.id))
    await db.flush()
    return _to_profile(agency)


def _assert_gstin_pan_immutable(agency: Agency, gstin: str | None, pan: str | None) -> None:
    """GSTIN and PAN are collected once at signup/verification and never
    editable afterward — matches compliance expectations for documents tied
    to escrow payouts. A resubmission of the same value is a no-op, not an
    error, since forms round-trip the existing value back unchanged."""
    if gstin and agency.gstin and gstin != agency.gstin:
        raise BadRequestError("GSTIN cannot be changed once submitted")
    if pan and agency.pan and pan != agency.pan:
        raise BadRequestError("PAN cannot be changed once submitted")


async def update_agency(
    db: AsyncSession, agency_id: str, user_id: str, req: UpdateAgencyRequest
) -> AgencyProfile:
    result = await db.execute(select(Agency).where(Agency.id == agency_id))
    agency = result.scalar_one_or_none()
    if not agency:
        raise NotFoundError("Agency")
    if agency.owner_id != user_id:
        raise ForbiddenError()

    _assert_gstin_pan_immutable(agency, req.gstin, req.pan)

    for field, value in req.model_dump(exclude_none=True).items():
        if field in ("specializations", "destinations") and isinstance(value, list):
            setattr(agency, field, value)
        elif hasattr(agency, field):
            setattr(agency, field, value)

    await db.flush()
    await invalidate(CacheKeys.agency_by_slug(agency.slug))
    return _to_profile(agency)


async def get_agency_members(db: AsyncSession, agency_id: str) -> list[dict]:
    result = await db.execute(
        select(AgencyMember)
        .options(selectinload(AgencyMember.user))
        .where(AgencyMember.agency_id == agency_id, AgencyMember.is_active == True)
    )
    return [
        {
            "id": m.id,
            "role": m.role,
            "isActive": m.is_active,
            "createdAt": m.created_at.isoformat() if m.created_at else "",
            "user": {
                "id": m.user.id,
                "fullName": m.user.display_name or m.user.username or "",
                "username": m.user.username,
                "avatarUrl": m.user.avatar_url,
            } if m.user else None,
        }
        for m in result.scalars().all()
    ]


async def verify_gst(gstin: str) -> GstVerifyResponse:
    result = await verify_gstin(gstin)
    return GstVerifyResponse(
        gstin=gstin,
        valid=result.get("valid", False),
        legal_name=result.get("legal_name"),
        trade_name=result.get("trade_name"),
        registration_date=result.get("registration_date"),
        status=result.get("status"),
    )


async def lookup_ifsc(code: str) -> IfscLookupResponse:
    result = await lookup_ifsc_code(code)
    return IfscLookupResponse(
        ifsc=code,
        valid=result.get("valid", False),
        bank=result.get("bank"),
        branch=result.get("branch"),
        address=result.get("address"),
        city=result.get("city"),
        state=result.get("state"),
        district=result.get("district"),
    )


async def submit_verification(
    db: AsyncSession,
    agency_id: str,
    user_id: str,
    payload: dict,
) -> AgencySummary:
    agency = await db.scalar(select(Agency).where(Agency.id == agency_id))
    if not agency:
        raise NotFoundError("Agency")
    if agency.owner_id != user_id:
        raise ForbiddenError("Only agency owner can submit verification")

    _assert_gstin_pan_immutable(agency, payload.get("gstin"), payload.get("pan"))

    for field in (
        "gstin",
        "pan",
        "tourism_license",
        "address",
        "city",
        "state",
        "phone",
        "email",
        "description",
    ):
        if field in payload and payload[field] is not None and hasattr(agency, field):
            setattr(agency, field, payload[field])

    agency.verification_status = "under_review"
    agency.verification_rejection_reason = None
    await db.flush()
    return _agency_to_summary(agency)


async def list_pending_verification_agencies(db: AsyncSession) -> list[AgencySummary]:
    """Admin-only (see require_admin() in app/api/v1/agencies.py). Feeds the
    minimal admin review queue — agencies currently awaiting a decision."""
    result = await db.execute(
        select(Agency)
        .where(Agency.verification_status == "under_review")
        .order_by(Agency.created_at.asc())
    )
    return [_agency_to_summary(a) for a in result.scalars().all()]


async def approve_verification(db: AsyncSession, agency_id: str) -> AgencySummary:
    """Admin-only (see require_admin() in app/api/v1/agencies.py). Previously
    there was no way for verification_status to ever leave 'under_review' —
    submit_verification set it, but nothing ever approved it."""
    agency = await db.scalar(select(Agency).where(Agency.id == agency_id))
    if not agency:
        raise NotFoundError("Agency")

    agency.verification_status = "verified"
    agency.verification_rejection_reason = None
    await db.flush()
    await invalidate(CacheKeys.agency_by_slug(agency.slug))

    # PRD trigger: send_compliance_approval_email
    from app.workers.tasks import send_compliance_approval_email_task
    send_compliance_approval_email_task.delay(agency.id)

    return _agency_to_summary(agency)


async def reject_verification(db: AsyncSession, agency_id: str, reason: str | None) -> AgencySummary:
    agency = await db.scalar(select(Agency).where(Agency.id == agency_id))
    if not agency:
        raise NotFoundError("Agency")

    agency.verification_status = "rejected"
    agency.verification_rejection_reason = reason
    await db.flush()
    await invalidate(CacheKeys.agency_by_slug(agency.slug))
    return _agency_to_summary(agency)


def _mask_account(account: str) -> str:
    cleaned = re.sub(r"\\s+", "", account or "")
    if len(cleaned) <= 4:
        return cleaned
    return ("*" * (len(cleaned) - 4)) + cleaned[-4:]


async def get_bank_record(db: AsyncSession, agency_id: str, user_id: str) -> dict:
    agency = await db.scalar(select(Agency).where(Agency.id == agency_id))
    if not agency:
        raise NotFoundError("Agency")
    if agency.owner_id != user_id:
        raise ForbiddenError("Only agency owner can view bank details")

    bank = await db.scalar(select(AgencyBankAccount).where(AgencyBankAccount.agency_id == agency_id))
    if not bank:
        raise NotFoundError("Bank record")

    is_verified = bank.verification_status == "VERIFIED"
    return {
        "id": bank.id,
        "accountHolderName": bank.account_holder_name,
        "bankName": bank.bank_name,
        "branchName": bank.branch_name,
        "maskedAccountNumber": _mask_account(bank.account_number_encrypted),
        "ifscCode": bank.ifsc_code,
        "razorpayAccountId": bank.razorpay_account_id,
        "verificationStatus": bank.verification_status,
        "nameMatchScore": 100 if is_verified else None,
        "nameMatchPassed": is_verified,
        "verifiedAt": bank.updated_at.isoformat() if is_verified and bank.updated_at else None,
        "retryCount": 0,
        "message": "Bank account verified" if is_verified else "Verification pending",
    }


async def _sync_razorpay_linked_account(
    agency: Agency, bank: AgencyBankAccount, contact_name: str
) -> tuple[str | None, str]:
    """Best-effort — bank verification must succeed in our system regardless
    of whether this does. Returns (razorpay_account_id_to_persist, status_message).
    Never raises: PaymentError from the Razorpay calls is caught here so a
    Razorpay-side failure can't block recording the agency's own bank details.

    contact_name must be an actual person's name (letters/spaces only) — confirmed
    empirically that Razorpay rejects business-style names ("... Pvt Ltd", anything
    with digits) with "contact name format is invalid". bank.account_holder_name is
    the wrong source for this: on Indian business bank accounts it's routinely the
    company name, not a person — the caller passes the agency owner's name instead.
    """
    if not (settings.razorpay_key_id and settings.razorpay_key_secret):
        return bank.razorpay_account_id, "Razorpay is not configured"

    required = {
        "email": agency.email, "phone": agency.phone, "address": agency.address,
        "city": agency.city, "state": agency.state, "postal_code": agency.postal_code,
    }
    missing = [field for field, value in required.items() if not value]
    if missing:
        logger.warning("Skipping Razorpay Route sync for agency %s — missing %s", agency.id, ", ".join(missing))
        return bank.razorpay_account_id, f"Add {', '.join(missing)} to your agency profile to enable automated payouts"

    try:
        account_id = bank.razorpay_account_id
        if not account_id:
            account = await create_linked_account(
                email=agency.email,
                phone=agency.phone,
                legal_business_name=agency.name,
                contact_name=contact_name,
                # Razorpay caps reference_id at 20 chars (confirmed empirically:
                # "The code may not be greater than 20 characters") — our ids are
                # 36-char UUIDs, so this is a lookup hint for Razorpay's side only,
                # not a full round-trippable key; razorpay_account_id is what we
                # actually key off on our end.
                reference_id=agency.id[:20],
                street1=agency.address,
                # Our schema only has one free-text address field; Razorpay
                # requires street2 non-empty (confirmed empirically — an
                # empty string is rejected), so reuse the city rather than
                # fabricate content that isn't true of the agency.
                street2=agency.city,
                city=agency.city,
                state=agency.state,
                postal_code=agency.postal_code,
            )
            account_id = account["id"]

        await configure_route_settlement(
            account_id,
            account_number=bank.account_number_encrypted,
            ifsc_code=bank.ifsc_code or "",
            beneficiary_name=bank.account_holder_name,
        )
        return account_id, "Synced with Razorpay Route"
    except PaymentError as exc:
        logger.error("Razorpay Route sync failed for agency %s: %s", agency.id, exc)
        return bank.razorpay_account_id, str(exc)


async def verify_bank_account(
    db: AsyncSession,
    agency_id: str,
    user_id: str,
    payload: dict,
) -> dict:
    agency = await db.scalar(select(Agency).where(Agency.id == agency_id))
    if not agency:
        raise NotFoundError("Agency")
    if agency.owner_id != user_id:
        raise ForbiddenError("Only agency owner can verify bank details")

    account_number = str(payload.get("accountNumber") or "").strip()
    ifsc_code = str(payload.get("ifscCode") or "").strip().upper()
    account_holder_name = str(payload.get("accountHolderName") or "").strip()
    bank_name = str(payload.get("bankName") or "").strip() or None
    branch_name = str(payload.get("branchName") or "").strip() or None
    # Explicit intent flag — the settings UI only sends this once the owner
    # clicks "Change bank details" on an already-verified record. Without it,
    # a verified record can't be silently overwritten by resubmitting the form.
    confirm_change = bool(payload.get("confirmChange"))

    if not account_number or not ifsc_code or not account_holder_name:
        raise BadRequestError("Account number, IFSC code, and account holder name are required")

    bank = await db.scalar(select(AgencyBankAccount).where(AgencyBankAccount.agency_id == agency_id))
    if bank and bank.verification_status == "VERIFIED" and not confirm_change:
        raise BadRequestError(
            "Bank details are already verified and locked. Use 'Change bank details' to submit new ones."
        )

    if not bank:
        bank = AgencyBankAccount(
            id=str(uuid.uuid4()),
            agency_id=agency_id,
            account_number_encrypted=account_number,
            ifsc_code=ifsc_code,
            account_holder_name=account_holder_name,
            bank_name=bank_name,
            branch_name=branch_name,
            verification_status="VERIFIED",
        )
        db.add(bank)
    else:
        bank.account_number_encrypted = account_number
        bank.ifsc_code = ifsc_code
        bank.account_holder_name = account_holder_name
        bank.bank_name = bank_name
        bank.branch_name = branch_name
        bank.verification_status = "VERIFIED"

    await db.flush()

    # Every verified (or re-verified) submission re-syncs the Razorpay Route
    # linked account — first-time creates it, later calls just refresh the
    # settlement bank details on the existing account (see
    # configure_route_settlement's idempotency note).
    owner = await db.scalar(select(User).where(User.id == agency.owner_id))
    contact_name = (owner.display_name or owner.username) if owner else None
    if not contact_name:
        razorpay_account_id, route_sync_message = bank.razorpay_account_id, "Missing agency owner contact name"
    else:
        razorpay_account_id, route_sync_message = await _sync_razorpay_linked_account(agency, bank, contact_name)
    bank.razorpay_account_id = razorpay_account_id
    await db.flush()

    return {
        "id": bank.id,
        "accountHolderName": bank.account_holder_name,
        "bankName": bank.bank_name,
        "branchName": bank.branch_name,
        "maskedAccountNumber": _mask_account(bank.account_number_encrypted),
        "ifscCode": bank.ifsc_code,
        "razorpayAccountId": bank.razorpay_account_id,
        "verificationStatus": "VERIFIED",
        "nameMatchScore": 100,
        "nameMatchPassed": True,
        "verifiedAt": datetime.now(UTC).isoformat(),
        "retryCount": 0,
        "message": "Bank account verified successfully",
        "routeSyncStatus": route_sync_message,
    }
