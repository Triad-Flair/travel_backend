from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel, CreatedAtMixin, TimestampsMixin


class LoyaltyPointsLedger(CreatedAtMixin, BaseModel):
    """loyalty_points_ledger: createdAt only."""
    __tablename__ = "loyalty_points_ledger"
    __table_args__ = {"extend_existing": True}

    user_id: Mapped[str] = mapped_column("userId", String(36), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column("eventType", String(30), nullable=False)
    points: Mapped[int] = mapped_column("points", Integer, nullable=False)
    description: Mapped[str | None] = mapped_column("description", Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column("idempotencyKey", String(100), nullable=True)
    group_id: Mapped[str | None] = mapped_column("groupId", String(36), nullable=True)
    payment_id: Mapped[str | None] = mapped_column("paymentId", String(36), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column("expiresAt", DateTime(timezone=True), nullable=True)
    expired: Mapped[bool] = mapped_column("expired", Boolean, default=False, nullable=False)

    user = relationship("User", lazy="noload", foreign_keys=[user_id],
                        primaryjoin="LoyaltyPointsLedger.user_id == User.id")


class ReferralLink(TimestampsMixin, BaseModel):
    __tablename__ = "referral_links"
    __table_args__ = {"extend_existing": True}

    user_id: Mapped[str] = mapped_column("userId", String(36), ForeignKey("users.id"), nullable=False, unique=True, index=True)
    code: Mapped[str] = mapped_column("code", String(20), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column("expiresAt", DateTime(timezone=True), nullable=True)
    used_at: Mapped[datetime | None] = mapped_column("usedAt", DateTime(timezone=True), nullable=True)
    used_by_user_id: Mapped[str | None] = mapped_column("usedByUserId", String(36), nullable=True)

    user = relationship("User", lazy="noload", foreign_keys=[user_id],
                        primaryjoin="ReferralLink.user_id == User.id")


class ReferralWallet(TimestampsMixin, BaseModel):
    __tablename__ = "referral_wallets"
    __table_args__ = {"extend_existing": True}

    user_id: Mapped[str] = mapped_column("userId", String(36), nullable=False, unique=True)
    balance: Mapped[int] = mapped_column("balance", Integer, default=0, nullable=False)
    total_earned: Mapped[int] = mapped_column("totalEarned", Integer, default=0, nullable=False)
    total_spent: Mapped[int] = mapped_column("totalSpent", Integer, default=0, nullable=False)

    user = relationship("User", lazy="noload", foreign_keys=[user_id],
                        primaryjoin="ReferralWallet.user_id == User.id")
    transactions = relationship("ReferralWalletTransaction", back_populates="wallet", lazy="noload")


class ReferralWalletTransaction(CreatedAtMixin, BaseModel):
    """referral_wallet_transactions: createdAt only."""
    __tablename__ = "referral_wallet_transactions"
    __table_args__ = {"extend_existing": True}

    wallet_id: Mapped[str] = mapped_column("walletId", String(36), ForeignKey("referral_wallets.id"), nullable=False, index=True)
    # Confirmed against the live DB (nothing had ever actually inserted a
    # row here before credit_referral_bonus): this is a real Postgres enum,
    # not a free-text varchar, with values referral_earned/checkout_spent/
    # admin_credit/admin_debit — not the "REFERRAL_BONUS" this code
    # originally assumed.
    type: Mapped[str] = mapped_column(
        "type",
        PgEnum("referral_earned", "checkout_spent", "admin_credit", "admin_debit",
               name="ReferralWalletTransactionType", create_type=False),
        nullable=False,
    )
    amount: Mapped[int] = mapped_column("amount", Integer, nullable=False)
    description: Mapped[str | None] = mapped_column("description", Text, nullable=True)
    reference_id: Mapped[str | None] = mapped_column("referenceId", String(100), nullable=True)

    wallet = relationship("ReferralWallet", back_populates="transactions")
