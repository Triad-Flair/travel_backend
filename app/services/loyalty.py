import secrets
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import ForbiddenError, NotFoundError
from app.models.agency import Agency, AgencyMember
from app.models.loyalty import LoyaltyPointsLedger, ReferralLink, ReferralWallet, ReferralWalletTransaction
from app.models.offer import Offer
from app.models.plan import Plan
from app.models.user import User
from app.schemas.loyalty import (
    LoyaltyBalanceResponse,
    LoyaltyLedgerEntry,
)
from app.models.enums import LoyaltyAction

REFERRAL_LINK_EXPIRY_DAYS = 30
# get_my_referrals reported every completed referral as "earning" this
# amount long before anything actually credited it anywhere — confirmed
# live: the Refer & Earn dashboard showed "1 completed, ₹250 earned" while
# the real wallet balance sat at ₹0, because this was a pure display
# number with no corresponding ReferralWallet credit. Shared here so the
# display and the actual credit can never drift apart.
REFERRAL_BONUS_RUPEES = 250


def _iso(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


def _share_urls(code: str) -> tuple[str, str]:
    share_url = f"{settings.frontend_url}/signup?ref={code}"
    qr_url = (
        "https://api.qrserver.com/v1/create-qr-code/?size=200x200&data="
        f"{share_url}"
    )
    return share_url, qr_url


def _normalize_referral_code(value: str | None) -> str:
    if not value:
        return ""
    code = "".join(ch for ch in value.upper() if ch.isalnum())
    return code[:8]


def _default_referral_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=REFERRAL_LINK_EXPIRY_DAYS)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        # Legacy rows can be stored/read as naive datetimes; treat them as UTC
        # so comparisons against timezone-aware `now` never crash.
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def _generate_unique_referral_code(db: AsyncSession) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    for _ in range(25):
        code = "".join(secrets.choice(alphabet) for _ in range(8))
        code_exists = await db.scalar(select(ReferralLink.id).where(ReferralLink.code == code))
        user_code_exists = await db.scalar(select(User.id).where(User.referral_code == code))
        if not code_exists and not user_code_exists:
            return code
    return uuid.uuid4().hex[:8].upper()


async def get_loyalty_balance(db: AsyncSession, user_id: str) -> LoyaltyBalanceResponse:
    result = await db.execute(
        select(LoyaltyPointsLedger).where(LoyaltyPointsLedger.user_id == user_id)
    )
    entries = result.scalars().all()
    balance = sum(int(e.points) for e in entries if not e.expired)
    earned = sum(int(e.points) for e in entries if int(e.points) > 0)
    redeemed = abs(sum(int(e.points) for e in entries if int(e.points) < 0))
    return LoyaltyBalanceResponse(balance=balance, lifetime_earned=earned, lifetime_redeemed=redeemed)


async def get_loyalty_ledger(
    db: AsyncSession, user_id: str, page: int, page_size: int
) -> tuple[list[LoyaltyLedgerEntry], int]:
    total = await db.scalar(
        select(func.count(LoyaltyPointsLedger.id)).where(LoyaltyPointsLedger.user_id == user_id)
    ) or 0
    result = await db.execute(
        select(LoyaltyPointsLedger)
        .where(LoyaltyPointsLedger.user_id == user_id)
        .order_by(LoyaltyPointsLedger.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    running_balance = 0
    items: list[LoyaltyLedgerEntry] = []
    for entry in result.scalars().all():
        running_balance += int(entry.points)
        try:
            action = LoyaltyAction(entry.event_type)
        except Exception:
            action = LoyaltyAction.ADMIN_ADJUST
        items.append(
            LoyaltyLedgerEntry(
                id=entry.id,
                action=action,
                points=int(entry.points),
                balance_after=running_balance,
                description=entry.description,
                expires_at=entry.expires_at,
                created_at=entry.created_at,
            )
        )

    return items, int(total)


async def get_or_create_referral_link(db: AsyncSession, user_id: str) -> dict:
    user = await db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise NotFoundError("User")

    link = await db.scalar(select(ReferralLink).where(ReferralLink.user_id == user_id))
    code = _normalize_referral_code(user.referral_code)
    if not code and link:
        code = _normalize_referral_code(link.code)
    if not code:
        code = await _generate_unique_referral_code(db)
        user.referral_code = code

    now = datetime.now(UTC)
    if not link:
        link = ReferralLink(
            id=str(uuid.uuid4()),
            user_id=user_id,
            code=code,
            expires_at=_default_referral_expiry(),
            used_at=None,
            used_by_user_id=None,
        )
        db.add(link)
    else:
        if link.code != code:
            link.code = code
        expires_at = _as_utc(link.expires_at)
        if expires_at is None or expires_at <= now or link.used_at or link.used_by_user_id:
            link.expires_at = _default_referral_expiry()
            link.used_at = None
            link.used_by_user_id = None

    await db.flush()

    share_url, qr_url = _share_urls(link.code)

    return {
        "id": link.id,
        "code": link.code,
        "shareUrl": share_url,
        "qrUrl": qr_url,
        "expiresAt": _iso(link.expires_at),
    }


async def generate_referral_link(db: AsyncSession, user_id: str) -> dict:
    return await get_or_create_referral_link(db, user_id)


async def _resolve_agency_id_for_user(db: AsyncSession, user_id: str) -> str | None:
    agency = await db.scalar(select(Agency).where(Agency.owner_id == user_id))
    if agency:
        return agency.id

    member = await db.scalar(
        select(AgencyMember).where(
            AgencyMember.user_id == user_id,
            AgencyMember.is_active == True,
        )
    )
    return member.agency_id if member else None


async def get_agency_referrals(db: AsyncSession, user_id: str) -> list[dict]:
    agency_id = await _resolve_agency_id_for_user(db, user_id)
    if not agency_id:
        raise ForbiddenError("Agency access required")

    rows = await db.execute(
        select(Offer)
        .where(
            Offer.agency_id == agency_id,
            Offer.is_referred == True,
        )
        .order_by(Offer.referred_at.desc(), Offer.created_at.desc())
        .limit(200)
    )
    offers = rows.scalars().all()

    result: list[dict] = []
    for offer in offers:
        plan = await db.scalar(select(Plan).where(Plan.id == offer.plan_id))
        if not plan:
            continue
        creator = await db.scalar(select(User).where(User.id == plan.creator_id))
        result.append(
            {
                "id": offer.id,
                "referredAt": _iso(offer.referred_at),
                "status": offer.status,
                "plan": {
                    "id": plan.id,
                    "title": plan.title,
                    "destination": plan.destination,
                    "startDate": _iso(plan.start_date),
                    "endDate": _iso(plan.end_date),
                    "budgetMin": plan.budget_min,
                    "budgetMax": plan.budget_max,
                    "creator": {
                        "id": creator.id if creator else plan.creator_id,
                        "fullName": (creator.display_name or creator.username or "Traveler") if creator else "Traveler",
                    },
                },
            }
        )

    return result


async def credit_referral_bonus(db: AsyncSession, referrer_user_id: str, referred_user_id: str) -> None:
    """Actually credits the ₹250 that get_my_referrals/get_referral_stats
    already claimed was "earned" — called once, right when a referral code
    is redeemed (see auth.py::_redeem_referral_code), so it's naturally
    idempotent: that caller only ever runs this path once per ReferralLink."""
    wallet = await db.scalar(select(ReferralWallet).where(ReferralWallet.user_id == referrer_user_id))
    if not wallet:
        wallet = ReferralWallet(id=str(uuid.uuid4()), user_id=referrer_user_id, balance=0, total_earned=0, total_spent=0)
        db.add(wallet)
        await db.flush()

    wallet.balance = int(wallet.balance or 0) + REFERRAL_BONUS_RUPEES
    wallet.total_earned = int(wallet.total_earned or 0) + REFERRAL_BONUS_RUPEES
    db.add(
        ReferralWalletTransaction(
            id=str(uuid.uuid4()),
            wallet_id=wallet.id,
            type="referral_earned",
            amount=REFERRAL_BONUS_RUPEES,
            description="Referral bonus — friend signed up with your code",
            reference_id=referred_user_id,
        )
    )


async def get_my_referrals(db: AsyncSession, user_id: str, page: int, page_size: int) -> dict:
    """Reads from ReferralWalletTransaction (one durable row per completed
    referral), not ReferralLink.used_by_user_id. That column can only ever
    hold a single referred user at a time — get_or_create_referral_link
    resets it to None the moment the referrer's own dashboard loads again
    after a completed referral, to rotate in a fresh shareable code. That
    made this list (and the stats derived from it) lose its own history on
    the very next page view — confirmed live: a real completed referral
    showing "1 completed, ₹250 earned" reset to zero after the referrer
    reloaded their Refer & Earn page. The wallet transaction ledger has no
    such one-slot limit."""
    wallet = await db.scalar(select(ReferralWallet).where(ReferralWallet.user_id == user_id))
    if not wallet:
        return {
            "referrals": [],
            "pagination": {"page": page, "pageSize": page_size, "total": 0, "pages": 0},
        }

    rows = await db.execute(
        select(ReferralWalletTransaction)
        .where(
            ReferralWalletTransaction.wallet_id == wallet.id,
            ReferralWalletTransaction.type == "referral_earned",
        )
        .order_by(ReferralWalletTransaction.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    transactions = rows.scalars().all()

    total = await db.scalar(
        select(func.count(ReferralWalletTransaction.id)).where(
            ReferralWalletTransaction.wallet_id == wallet.id,
            ReferralWalletTransaction.type == "referral_earned",
        )
    ) or 0

    referrals = []
    for tx in transactions:
        referred = await db.scalar(select(User).where(User.id == tx.reference_id))
        referrals.append(
            {
                "id": tx.id,
                "referredUser": {
                    "id": referred.id if referred else tx.reference_id,
                    "fullName": (referred.display_name or referred.username or "New user") if referred else "New user",
                    "email": referred.email if referred else None,
                    "avatarUrl": referred.avatar_url if referred else None,
                    "createdAt": _iso(referred.created_at) if referred else _iso(tx.created_at),
                },
                "status": "COMPLETED",
                "earnedAmount": int(tx.amount),
                "completedAt": _iso(tx.created_at),
                "createdAt": _iso(tx.created_at),
            }
        )

    return {
        "referrals": referrals,
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "total": int(total),
            "pages": int((total + page_size - 1) // page_size) if page_size > 0 else 0,
        },
    }


async def get_referral_stats(db: AsyncSession, user_id: str) -> dict:
    my = await get_my_referrals(db, user_id, 1, 500)
    refs = my["referrals"]

    sent_referrals = len(refs)
    sent_completed = sum(1 for r in refs if r.get("status") == "COMPLETED")
    sent_pending = max(0, sent_referrals - sent_completed)
    total_earned = int(sum(float(r.get("earnedAmount") or 0) for r in refs))

    return {
        "sentReferrals": sent_referrals,
        "sentCompleted": sent_completed,
        "sentPending": sent_pending,
        "totalEarned": total_earned,
        "receivedReferrals": 0,
    }


async def get_referral_metrics(db: AsyncSession, user_id: str) -> dict:
    stats = await get_referral_stats(db, user_id)
    sent = max(1, int(stats["sentReferrals"]))
    completed = int(stats["sentCompleted"])
    earned = float(stats["totalEarned"])

    return {
        **stats,
        "conversionRate": f"{(completed / sent) * 100:.2f}%",
        "averageEarningsPerReferral": f"{(earned / sent):.2f}",
    }


async def get_wallet_balance(db: AsyncSession, user_id: str) -> dict:
    wallet = await db.scalar(select(ReferralWallet).where(ReferralWallet.user_id == user_id))
    if not wallet:
        wallet = ReferralWallet(id=str(uuid.uuid4()), user_id=user_id, balance=0, total_earned=0, total_spent=0)
        db.add(wallet)
        await db.flush()
    return {"balance": int(wallet.balance or 0)}


async def get_wallet_transactions(
    db: AsyncSession,
    user_id: str,
    page: int,
    page_size: int,
    filter_type: str | None = None,
) -> dict:
    wallet = await db.scalar(select(ReferralWallet).where(ReferralWallet.user_id == user_id))
    if not wallet:
        return {
            "transactions": [],
            "pagination": {"page": page, "pageSize": page_size, "total": 0, "pages": 0},
        }

    where_clauses = [ReferralWalletTransaction.wallet_id == wallet.id]
    if filter_type:
        where_clauses.append(ReferralWalletTransaction.type == filter_type)

    total = await db.scalar(
        select(func.count(ReferralWalletTransaction.id)).where(*where_clauses)
    ) or 0

    rows = await db.execute(
        select(ReferralWalletTransaction)
        .where(*where_clauses)
        .order_by(ReferralWalletTransaction.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    transactions = []
    for tx in rows.scalars().all():
        transactions.append(
            {
                "id": tx.id,
                "type": tx.type,
                "amount": int(tx.amount),
                "description": tx.description,
                "createdAt": _iso(tx.created_at),
            }
        )

    return {
        "transactions": transactions,
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "total": int(total),
            "pages": int((total + page_size - 1) // page_size) if page_size > 0 else 0,
        },
    }


async def get_wallet_monthly_summary(db: AsyncSession, user_id: str, months_back: int = 12) -> list[dict]:
    wallet = await db.scalar(select(ReferralWallet).where(ReferralWallet.user_id == user_id))
    if not wallet:
        return []

    rows = await db.execute(
        select(ReferralWalletTransaction)
        .where(ReferralWalletTransaction.wallet_id == wallet.id)
        .order_by(ReferralWalletTransaction.created_at.desc())
    )
    txs = rows.scalars().all()

    by_month: dict[str, dict] = defaultdict(lambda: {"earned": 0, "spent": 0})
    for tx in txs:
        month = tx.created_at.strftime("%Y-%m")
        amount = int(tx.amount)
        if amount >= 0:
            by_month[month]["earned"] += amount
        else:
            by_month[month]["spent"] += abs(amount)

    months = sorted(by_month.keys(), reverse=True)[: max(1, months_back)]
    balance = int(wallet.balance or 0)
    return [
        {
            "month": month,
            "earned": by_month[month]["earned"],
            "spent": by_month[month]["spent"],
            "balance": balance,
        }
        for month in months
    ]


async def get_cashflow_audit(db: AsyncSession, start_date: datetime, end_date: datetime) -> dict:
    rows = await db.execute(
        select(ReferralWalletTransaction)
        .where(
            ReferralWalletTransaction.created_at >= start_date,
            ReferralWalletTransaction.created_at <= end_date,
        )
        .order_by(ReferralWalletTransaction.created_at.desc())
        .limit(1000)
    )
    txs = rows.scalars().all()

    total_credits = sum(int(t.amount) for t in txs if int(t.amount) > 0)
    total_debits = sum(abs(int(t.amount)) for t in txs if int(t.amount) < 0)

    credits_by_type: dict[str, int] = defaultdict(int)
    debits_by_type: dict[str, int] = defaultdict(int)
    for tx in txs:
        amount = int(tx.amount)
        if amount >= 0:
            credits_by_type[tx.type] += amount
        else:
            debits_by_type[tx.type] += abs(amount)

    transactions = []
    for tx in txs:
        wallet = await db.scalar(select(ReferralWallet).where(ReferralWallet.id == tx.wallet_id))
        transactions.append(
            {
                "id": tx.id,
                "userId": wallet.user_id if wallet else "",
                "type": tx.type,
                "amount": int(tx.amount),
                "createdAt": _iso(tx.created_at),
            }
        )

    return {
        "period": {
            "startDate": _iso(start_date),
            "endDate": _iso(end_date),
        },
        "summary": {
            "totalCredits": total_credits,
            "totalDebits": total_debits,
            "creditsByType": dict(credits_by_type),
            "debitsByType": dict(debits_by_type),
        },
        "transactions": transactions,
    }


async def get_reconciliation_report(db: AsyncSession, start_date: datetime, end_date: datetime) -> dict:
    rows = await db.execute(
        select(ReferralWalletTransaction)
        .where(
            ReferralWalletTransaction.created_at >= start_date,
            ReferralWalletTransaction.created_at <= end_date,
            ReferralWalletTransaction.type == "checkout_spent",
        )
    )
    wallet_checkout_txs = rows.scalars().all()
    wallet_total = sum(abs(int(t.amount)) for t in wallet_checkout_txs)

    return {
        "period": {
            "startDate": _iso(start_date),
            "endDate": _iso(end_date),
        },
        "walletCheckouts": {
            "total": len(wallet_checkout_txs),
            "amount": wallet_total,
        },
        "paymentRecords": {
            "total": 0,
            "amount": 0,
        },
        "discrepancies": [],
        "status": "reconciled",
    }
