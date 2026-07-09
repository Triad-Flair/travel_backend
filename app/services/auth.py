import hashlib
import re
import uuid
from datetime import UTC, datetime, timedelta

from slugify import slugify
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import (
    create_access_token,
    create_action_token,
    create_refresh_token,
    decode_action_token,
    decode_refresh_token,
    hash_password,
    verify_password,
)
from app.exceptions import BadRequestError, ConflictError, InvalidTokenError, NotFoundError, UnauthorizedError
from app.lib.email import (
    send_agency_welcome_email,
    send_password_reset_email,
    send_traveler_welcome_email,
    send_verification_email,
)
from app.models.user import User
from app.schemas.auth import (
    AadhaarVerificationRequest,
    AadhaarVerificationResponse,
    AgencySignupRequest,
    AuthActionResponse,
    AuthSessionResponse,
    ForgotPasswordRequest,
    LoginRequest,
    ResetPasswordRequest,
    SignupMessageResponse,
    TravelerSignupRequest,
    UpdateProfileRequest,
    UserInSession,
    AgencySummaryInSession,
)


def _build_user_in_session(user: User, agency=None) -> UserInSession:
    agency_in_session = None
    if agency:
        agency_in_session = AgencySummaryInSession(
            id=agency.id,
            name=agency.name,
            slug=agency.slug,
            logo_url=agency.logo_url,
            verification=agency.verification_status,
        )
    return UserInSession(
        id=user.id,
        full_name=user.display_name or user.username or "",
        username=user.username,
        avatar_url=user.avatar_url,
        phone=user.phone,
        email=user.email,
        bio=user.bio,
        gender=user.gender,
        city=user.location,
        travel_preferences=user.travel_style,
        verification=user.verification_tier,
        agency=agency_in_session,
    )


def _build_session(user: User, agency_id: str | None = None, agency=None) -> AuthSessionResponse:
    role = "agency_admin" if agency_id else "user"
    payload = {"sub": user.id, "role": role}
    if agency_id:
        payload["agencyId"] = agency_id
    return AuthSessionResponse(
        access_token=create_access_token(payload),
        refresh_token=create_refresh_token(payload),
        user=_build_user_in_session(user, agency),
        agency_id=agency_id,
        role=role,
    )


def _password_fingerprint(password_hash: str | None) -> str:
    return hashlib.sha256((password_hash or "no-password").encode()).hexdigest()[:24]


def _display_name_for_email(user: User) -> str:
    return user.display_name or user.username or "Traveler"


def _parse_date_of_birth(value: str | None) -> datetime | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BadRequestError("date_of_birth must be a valid ISO-8601 date or datetime") from exc

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _format_date_of_birth(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _issue_email_verification_token(user: User) -> str:
    if not user.email:
        raise BadRequestError("User does not have an email address")
    nonce = uuid.uuid4().hex
    user.email_verified = False
    user.email_verification_token = nonce
    return create_action_token(
        {"sub": user.id, "email": user.email, "nonce": nonce},
        purpose="verify_email",
        expires_in=timedelta(hours=max(1, int(settings.email_verification_expire_hours))),
    )


def _issue_password_reset_token(user: User) -> str:
    if not user.email:
        raise BadRequestError("User does not have an email address")
    return create_action_token(
        {"sub": user.id, "email": user.email, "pwd": _password_fingerprint(user.password_hash)},
        purpose="reset_password",
        expires_in=timedelta(minutes=max(5, int(settings.password_reset_expire_minutes))),
    )


async def signup_traveler(db: AsyncSession, req: TravelerSignupRequest) -> SignupMessageResponse:
    username = req.username.strip().lower()
    email = req.email.strip().lower() if req.email else None
    phone = req.phone.strip() if req.phone else None

    if phone and await db.scalar(select(User).where(User.phone == phone)):
        raise ConflictError("Phone already registered")
    if username and await db.scalar(select(User).where(func.lower(User.username) == username)):
        raise ConflictError("Username already taken")
    if email and await db.scalar(select(User).where(func.lower(User.email) == email)):
        raise ConflictError("Email already registered")

    user = User(
        id=str(uuid.uuid4()),
        phone=phone,
        email=email,
        username=username,
        display_name=req.full_name,
        password_hash=hash_password(req.password),
        date_of_birth=_parse_date_of_birth(req.date_of_birth),
        gender=req.gender,
        location=req.city,
        travel_style=req.travel_preferences,
        bio=req.bio,
        avatar_url=req.avatar_url,
        referral_code=req.referral_code,
    )
    db.add(user)
    await db.flush()

    if email:
        verification_token = _issue_email_verification_token(user)
        await send_verification_email(email, req.full_name, verification_token)
        return SignupMessageResponse(message="Account created. Please verify your email before signing in.")

    user.email_verified = True
    return SignupMessageResponse(message="Account created successfully.")


async def signup_agency_owner(db: AsyncSession, req: AgencySignupRequest) -> SignupMessageResponse:
    from app.models.agency import Agency, AgencyMember, AgencyWallet

    username = req.username.strip().lower()
    email = req.email.strip().lower()
    phone = req.phone.strip() if req.phone else None
    agency_email = req.agency_email.strip().lower() if req.agency_email else None
    agency_phone = req.agency_phone.strip() if req.agency_phone else None

    if await db.scalar(select(User).where(func.lower(User.email) == email)):
        raise ConflictError("Email already registered")
    if await db.scalar(select(User).where(func.lower(User.username) == username)):
        raise ConflictError("Username already taken")
    if phone and await db.scalar(select(User).where(User.phone == phone)):
        raise ConflictError("Phone already registered")

    user = User(
        id=str(uuid.uuid4()),
        email=email,
        username=username,
        display_name=req.full_name,
        phone=phone,
        password_hash=hash_password(req.password),
        date_of_birth=_parse_date_of_birth(req.date_of_birth),
        gender=req.gender,
        location=req.city or req.agency_city,
        bio=req.bio,
        avatar_url=req.avatar_url,
        travel_style=req.travel_preferences,
    )
    db.add(user)
    await db.flush()

    slug = slugify(req.agency_name) + "-" + user.id[:8]
    agency = Agency(
        id=str(uuid.uuid4()),
        owner_id=user.id,
        name=req.agency_name,
        slug=slug,
        description=req.agency_description,
        gstin=req.gstin,
        pan=req.pan,
        tourism_license=req.tourism_license,
        address=req.agency_address,
        city=req.agency_city,
        state=req.agency_state,
        phone=agency_phone or phone,
        email=agency_email or email,
        specializations=req.specializations or None,
        destinations=req.destinations or None,
    )
    db.add(agency)
    await db.flush()

    db.add(AgencyMember(id=str(uuid.uuid4()), agency_id=agency.id, user_id=user.id, role="ADMIN"))
    db.add(AgencyWallet(id=str(uuid.uuid4()), agency_id=agency.id))
    await db.flush()

    verification_token = _issue_email_verification_token(user)
    await send_verification_email(email, req.full_name, verification_token)

    return SignupMessageResponse(message="Agency account created. Please verify your email before signing in.")


async def login(db: AsyncSession, req: LoginRequest) -> AuthSessionResponse:
    from app.models.agency import Agency, AgencyMember

    normalized_identifier = req.identifier.strip()
    if not normalized_identifier:
        raise UnauthorizedError("Email or username is required")
    normalized_lower = normalized_identifier.lower()
    looks_like_phone = bool(re.fullmatch(r"[6-9]\d{9}", normalized_identifier))

    lookup_conditions = [
        func.lower(User.email) == normalized_lower,
        func.lower(User.username) == normalized_lower,
    ]
    if looks_like_phone:
        lookup_conditions.append(User.phone == normalized_identifier)

    user = await db.scalar(
        select(User).where(or_(*lookup_conditions))
    )
    if not user:
        raise UnauthorizedError("No account found for this email/username")
    if not user.password_hash:
        raise UnauthorizedError("This account has no password set. Please reset password.")
    if not user.is_active:
        raise UnauthorizedError("Account is deactivated")
    if not verify_password(req.password, user.password_hash):
        raise UnauthorizedError("Incorrect password")

    # Keep Express parity: block only accounts created after email verification flow
    # was introduced (those with a non-null emailVerificationToken).
    if not user.email_verified and user.email_verification_token is not None:
        raise UnauthorizedError(
            "Please verify your email address before logging in. Check your inbox for the verification link."
        )

    agency_id = None
    agency = None
    member = await db.scalar(
        select(AgencyMember).where(AgencyMember.user_id == user.id, AgencyMember.is_active == True)
    )
    if member:
        agency_id = member.agency_id
        agency = await db.scalar(select(Agency).where(Agency.id == agency_id))

    return _build_session(user, agency_id, agency)


async def refresh_tokens(db: AsyncSession, refresh_token: str) -> AuthSessionResponse:
    from app.models.agency import Agency

    payload = decode_refresh_token(refresh_token)
    user_id = payload.get("sub")

    user = await db.scalar(select(User).where(User.id == user_id))
    if not user or not user.is_active:
        raise UnauthorizedError()

    agency_id = payload.get("agencyId")
    agency = None
    if agency_id:
        agency = await db.scalar(select(Agency).where(Agency.id == agency_id))

    return _build_session(user, agency_id, agency)


async def get_me(db: AsyncSession, user_id: str) -> UserInSession:
    from app.models.agency import Agency, AgencyMember

    user = await db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise NotFoundError("User")

    agency = None
    member = await db.scalar(
        select(AgencyMember).where(AgencyMember.user_id == user.id, AgencyMember.is_active == True)
    )
    if member:
        agency = await db.scalar(select(Agency).where(Agency.id == member.agency_id))

    return _build_user_in_session(user, agency)


async def get_user_aadhaar_hash(db: AsyncSession, user_id: str) -> str | None:
    user = await db.scalar(select(User).where(User.id == user_id))
    return user.aadhaar_hash if user else None


async def update_profile(db: AsyncSession, user_id: str, req: UpdateProfileRequest) -> UserInSession:
    user = await db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise NotFoundError("User")

    verification_token: str | None = None

    if req.full_name is not None:
        user.display_name = req.full_name
    if req.bio is not None:
        user.bio = req.bio
    if req.avatar_url is not None:
        user.avatar_url = req.avatar_url
    if req.city is not None:
        user.location = req.city
    if req.travel_preferences is not None:
        user.travel_style = req.travel_preferences
    if req.email is not None:
        normalized_email = req.email.strip().lower()
        existing = await db.scalar(
            select(User).where(func.lower(User.email) == normalized_email, User.id != user_id)
        )
        if existing:
            raise ConflictError("Email already registered")
        if normalized_email != (user.email or "").strip().lower():
            user.email = normalized_email
            verification_token = _issue_email_verification_token(user)
    if req.gender is not None:
        user.gender = req.gender
    if req.date_of_birth is not None:
        user.date_of_birth = _parse_date_of_birth(req.date_of_birth)

    await db.flush()
    if verification_token and user.email:
        await send_verification_email(user.email, _display_name_for_email(user), verification_token)
    return await get_me(db, user_id)


async def verify_email(db: AsyncSession, token: str) -> dict:
    from app.models.agency import Agency, AgencyMember

    cleaned = (token or "").strip()
    if not cleaned:
        raise UnauthorizedError("Verification token is required")

    payload = decode_action_token(cleaned, expected_purpose="verify_email")
    user_id = str(payload.get("sub") or "").strip()
    nonce = str(payload.get("nonce") or "").strip()
    email = str(payload.get("email") or "").strip().lower()
    if not user_id or not nonce or not email:
        raise InvalidTokenError()

    user = await db.scalar(select(User).where(User.id == user_id))
    if not user or not user.email or user.email.strip().lower() != email:
        raise InvalidTokenError()
    if user.email_verification_token != nonce:
        raise InvalidTokenError()

    user.email_verified = True
    user.email_verification_token = None
    await db.flush()

    member = await db.scalar(
        select(AgencyMember).where(AgencyMember.user_id == user.id, AgencyMember.is_active == True)
    )
    display_name = _display_name_for_email(user)
    if member:
        agency = await db.scalar(select(Agency).where(Agency.id == member.agency_id))
        if agency:
            await send_agency_welcome_email(user.email, display_name, agency.name)
    else:
        await send_traveler_welcome_email(user.email, display_name)

    return {"message": "Email verified successfully. You can log in now."}


async def resend_verification(db: AsyncSession, req) -> AuthActionResponse:
    email = req.email.strip().lower()
    generic_message = "If an account exists for this email, a verification link has been sent."
    user = await db.scalar(select(User).where(func.lower(User.email) == email))
    if not user or not user.is_active:
        return AuthActionResponse(message=generic_message, sent=True)
    if user.email_verified:
        return AuthActionResponse(message="This email is already verified. You can sign in.", sent=True)

    token = _issue_email_verification_token(user)
    await db.flush()
    sent = await send_verification_email(user.email, _display_name_for_email(user), token)
    return AuthActionResponse(
        message="Verification email sent. Please check your inbox." if sent else "Could not send verification email right now. Please try again shortly.",
        sent=bool(sent),
    )


async def request_password_reset(db: AsyncSession, req: ForgotPasswordRequest) -> AuthActionResponse:
    email = req.email.strip().lower()
    generic_message = "If an account exists for this email, a password reset link has been sent."
    user = await db.scalar(select(User).where(func.lower(User.email) == email))
    if not user or not user.is_active or not user.email:
        return AuthActionResponse(message=generic_message, sent=True)

    token = _issue_password_reset_token(user)
    sent = await send_password_reset_email(user.email, _display_name_for_email(user), token)
    return AuthActionResponse(
        message=generic_message if sent else "Could not send password reset email right now. Please try again shortly.",
        sent=bool(sent),
    )


async def reset_password(db: AsyncSession, req: ResetPasswordRequest) -> SignupMessageResponse:
    payload = decode_action_token(req.token.strip(), expected_purpose="reset_password")
    user_id = str(payload.get("sub") or "").strip()
    email = str(payload.get("email") or "").strip().lower()
    password_fingerprint = str(payload.get("pwd") or "").strip()
    if not user_id or not email or not password_fingerprint:
        raise InvalidTokenError()

    user = await db.scalar(select(User).where(User.id == user_id))
    if not user or not user.is_active or not user.email or user.email.strip().lower() != email:
        raise InvalidTokenError()
    if password_fingerprint != _password_fingerprint(user.password_hash):
        raise InvalidTokenError()

    user.password_hash = hash_password(req.password)
    await db.flush()
    return SignupMessageResponse(message="Password updated successfully. You can sign in now.")


async def verify_aadhaar_for_user(
    db: AsyncSession,
    user_id: str,
    req: AadhaarVerificationRequest,
) -> AadhaarVerificationResponse:
    if not req.consent:
        raise UnauthorizedError("Consent is required for Aadhaar verification")

    user = await db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise NotFoundError("User")

    aadhaar_hash = hashlib.sha256(req.aadhaar_number.encode()).hexdigest()
    user.aadhaar_hash = aadhaar_hash
    user.verification_tier = "VERIFIED"
    if req.date_of_birth:
        user.date_of_birth = _parse_date_of_birth(req.date_of_birth)
    if req.full_name:
        user.display_name = req.full_name
    await db.flush()

    from app.models.agency import Agency, AgencyMember

    agency = None
    member = await db.scalar(
        select(AgencyMember).where(AgencyMember.user_id == user.id, AgencyMember.is_active == True)
    )
    if member:
        agency = await db.scalar(select(Agency).where(Agency.id == member.agency_id))

    return AadhaarVerificationResponse(
        tier="VERIFIED",
        provider="mock",
        masked_aadhaar=f"XXXX-XXXX-{req.aadhaar_number[-4:]}",
        full_name=user.display_name or req.full_name,
        date_of_birth=_format_date_of_birth(user.date_of_birth),
        agency=AgencySummaryInSession(
            id=agency.id,
            name=agency.name,
            slug=agency.slug,
            logo_url=agency.logo_url,
            verification=agency.verification_status,
        ) if agency else None,
    )
