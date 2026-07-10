from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel, CreatedAtMixin, TimestampsMixin


class Agency(TimestampsMixin, BaseModel):
    __tablename__ = "agencies"
    __table_args__ = {"extend_existing": True}

    owner_id: Mapped[str] = mapped_column("ownerId", String(36), ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column("name", String(200), nullable=False)
    slug: Mapped[str] = mapped_column("slug", String(200), nullable=False, index=True, unique=True)
    logo_url: Mapped[str | None] = mapped_column("logoUrl", Text, nullable=True)
    description: Mapped[str | None] = mapped_column("description", Text, nullable=True)
    gstin: Mapped[str | None] = mapped_column("gstin", String(15), nullable=True)
    pan: Mapped[str | None] = mapped_column("pan", String(10), nullable=True)
    tourism_license: Mapped[str | None] = mapped_column("tourismLicense", String(100), nullable=True)
    address: Mapped[str | None] = mapped_column("address", Text, nullable=True)
    city: Mapped[str | None] = mapped_column("city", String(100), nullable=True)
    state: Mapped[str | None] = mapped_column("state", String(100), nullable=True)
    phone: Mapped[str | None] = mapped_column("phone", String(20), nullable=True)
    email: Mapped[str | None] = mapped_column("email", String(255), nullable=True)
    specializations: Mapped[list | dict | None] = mapped_column("specializations", JSONB, nullable=True)
    destinations: Mapped[list | dict | None] = mapped_column("destinations", JSONB, nullable=True)
    verification_status: Mapped[str] = mapped_column("verification", String(30), default="pending", nullable=False)
    verification_rejection_reason: Mapped[str | None] = mapped_column("verificationRejectionReason", Text, nullable=True)
    avg_rating: Mapped[float] = mapped_column("avgRating", Float, default=0.0, nullable=False)
    review_count: Mapped[int] = mapped_column("totalReviews", Integer, default=0, nullable=False)
    total_trips: Mapped[int] = mapped_column("totalTrips", Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column("isActive", Boolean, default=True, nullable=False)

    # Non-DB computed attrs
    is_pro: bool = False
    gstin_verified: bool = False
    cover_image_url: str | None = None

    members = relationship("AgencyMember", back_populates="agency", lazy="noload")
    packages = relationship("Package", back_populates="agency", lazy="noload")
    offers = relationship("Offer", back_populates="agency", lazy="noload")


class AgencyMember(BaseModel):
    __tablename__ = "agency_members"
    __table_args__ = {"extend_existing": True}

    agency_id: Mapped[str] = mapped_column("agencyId", String(36), ForeignKey("agencies.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column("userId", String(36), ForeignKey("users.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(
        "role",
        PgEnum("ADMIN", "MANAGER", "AGENT", "FINANCE", name="AgencyMemberRole", create_type=False),
        default="AGENT",
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column("isActive", Boolean, default=True, nullable=False)
    invited_by: Mapped[str | None] = mapped_column("invitedBy", String(36), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(
        "joinedAt",
        DateTime(timezone=False),
        server_default=func.now(),
        nullable=False,
    )

    agency = relationship("Agency", back_populates="members")
    user = relationship("User", lazy="noload")


class AgencyWallet(BaseModel):
    """agency_wallets has updatedAt but NO createdAt."""
    __tablename__ = "agency_wallets"
    __table_args__ = {"extend_existing": True}

    agency_id: Mapped[str] = mapped_column("agencyId", String(36), ForeignKey("agencies.id"), nullable=False, unique=True)
    pending_balance: Mapped[int] = mapped_column("pendingBalance", Integer, default=0, nullable=False)
    available_balance: Mapped[int] = mapped_column("availableBalance", Integer, default=0, nullable=False)
    total_earned: Mapped[int] = mapped_column("totalEarned", Integer, default=0, nullable=False)
    total_commission: Mapped[int] = mapped_column("totalCommission", Integer, default=0, nullable=False)
    security_deposit: Mapped[int] = mapped_column("securityDeposit", Integer, default=0, nullable=False)
    payout_mode: Mapped[str] = mapped_column(
        "payoutMode",
        PgEnum("TRUST", "PRO", name="WalletPayoutMode", create_type=False),
        default="TRUST",
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        "updatedAt",
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    agency = relationship("Agency", lazy="noload")


class AgencyTransaction(CreatedAtMixin, BaseModel):
    __tablename__ = "agency_transactions"
    __table_args__ = {"extend_existing": True}

    wallet_id: Mapped[str] = mapped_column("walletId", String(36), nullable=False, index=True)
    type: Mapped[str] = mapped_column("type", String(30), nullable=False)
    amount: Mapped[int] = mapped_column("amount", Integer, nullable=False)
    description: Mapped[str | None] = mapped_column("description", Text, nullable=True)
    group_id: Mapped[str | None] = mapped_column("groupId", String(36), nullable=True)
    payment_id: Mapped[str | None] = mapped_column("paymentId", String(36), nullable=True)
    razorpay_transfer_id: Mapped[str | None] = mapped_column("razorpayTransferId", String(100), nullable=True)

    wallet = relationship("AgencyWallet", lazy="noload", foreign_keys=[wallet_id],
                          primaryjoin="AgencyTransaction.wallet_id == AgencyWallet.id")


class AgencyTrustProfile(TimestampsMixin, BaseModel):
    __tablename__ = "agency_trust_profiles"
    __table_args__ = {"extend_existing": True}

    agency_id: Mapped[str] = mapped_column("agencyId", String(36), ForeignKey("agencies.id"), nullable=False, unique=True)
    trust_score: Mapped[float] = mapped_column("trustScore", Float, default=0.0, nullable=False)
    eligible_for_pro: Mapped[bool] = mapped_column("eligibleForPro", Boolean, default=False, nullable=False)
    completed_trips_count: Mapped[int] = mapped_column("completedTripsCount", Integer, default=0, nullable=False)

    agency = relationship("Agency", lazy="noload")


class AgencyBankAccount(TimestampsMixin, BaseModel):
    __tablename__ = "agency_bank_accounts"
    __table_args__ = {"extend_existing": True}

    agency_id: Mapped[str] = mapped_column("agencyId", String(36), ForeignKey("agencies.id"), nullable=False, unique=True)
    account_number_encrypted: Mapped[str] = mapped_column("encryptedAccountNumber", Text, nullable=False)
    ifsc_code: Mapped[str | None] = mapped_column("encryptedIfsc", Text, nullable=True)
    account_holder_name: Mapped[str] = mapped_column("accountHolderName", String(100), nullable=False)
    bank_name: Mapped[str | None] = mapped_column("bankName", String(100), nullable=True)
    branch_name: Mapped[str | None] = mapped_column("branchName", String(150), nullable=True)
    # DB column is a real enum (PENDING/VERIFIED/FAILED/NAME_MISMATCH/
    # MAX_RETRIES_EXCEEDED) — this was previously mismapped as Boolean,
    # which made every INSERT/UPDATE fail with a DatatypeMismatchError.
    verification_status: Mapped[str] = mapped_column(
        "verificationStatus",
        PgEnum("PENDING", "VERIFIED", "FAILED", "NAME_MISMATCH", "MAX_RETRIES_EXCEEDED", name="BankVerificationStatus", create_type=False),
        default="PENDING",
        nullable=False,
    )
    razorpay_account_id: Mapped[str | None] = mapped_column("razorpayAccountId", String(50), nullable=True)

    agency = relationship("Agency", lazy="noload")


class KycDocument(TimestampsMixin, BaseModel):
    __tablename__ = "kyc_documents"
    __table_args__ = {"extend_existing": True}

    agency_id: Mapped[str] = mapped_column("agencyId", String(36), ForeignKey("agencies.id"), nullable=False, index=True)
    document_type: Mapped[str] = mapped_column("documentType", String(30), nullable=False)
    s3_key: Mapped[str] = mapped_column("s3Key", Text, nullable=False)
    original_filename: Mapped[str | None] = mapped_column("originalFilename", String(255), nullable=True)
    status: Mapped[str] = mapped_column("status", String(20), default="PENDING", nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column("rejectionReason", Text, nullable=True)

    agency = relationship("Agency", lazy="noload")


class InsuranceQuote(TimestampsMixin, BaseModel):
    __tablename__ = "insurance_quotes"
    __table_args__ = {"extend_existing": True}

    agency_id: Mapped[str] = mapped_column("agencyId", String(36), ForeignKey("agencies.id"), nullable=False)
    provider: Mapped[str] = mapped_column("provider", String(100), nullable=False)
    premium_amount: Mapped[int] = mapped_column("premiumAmount", Integer, default=0, nullable=False)
    coverage_amount: Mapped[int] = mapped_column("coverageAmount", Integer, default=0, nullable=False)
    pax_count: Mapped[int] = mapped_column("paxCount", Integer, default=1, nullable=False)
    start_date: Mapped[str] = mapped_column("startDate", String(20), nullable=False)
    end_date: Mapped[str] = mapped_column("endDate", String(20), nullable=False)
    status: Mapped[str] = mapped_column("status", String(20), default="QUOTED", nullable=False)

    agency = relationship("Agency", lazy="noload")


class AgencyCustomer(TimestampsMixin, BaseModel):
    __tablename__ = "agency_customers"
    __table_args__ = {"extend_existing": True}

    agency_id: Mapped[str] = mapped_column("agencyId", String(36), ForeignKey("agencies.id"), nullable=False)
    name: Mapped[str] = mapped_column("name", String(100), nullable=False)
    email: Mapped[str | None] = mapped_column("email", String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column("phone", String(20), nullable=True)
    notes: Mapped[str | None] = mapped_column("notes", Text, nullable=True)

    agency = relationship("Agency", lazy="noload")


class AgencyCampaign(TimestampsMixin, BaseModel):
    __tablename__ = "agency_campaigns"
    __table_args__ = {"extend_existing": True}

    agency_id: Mapped[str] = mapped_column("agencyId", String(36), ForeignKey("agencies.id"), nullable=False)
    title: Mapped[str] = mapped_column("title", String(200), nullable=False)
    status: Mapped[str] = mapped_column("status", String(20), default="DRAFT", nullable=False)

    agency = relationship("Agency", lazy="noload")


class FraudRiskFlag(TimestampsMixin, BaseModel):
    __tablename__ = "fraud_risk_flags"
    __table_args__ = {"extend_existing": True}

    agency_id: Mapped[str] = mapped_column("agencyId", String(36), ForeignKey("agencies.id"), nullable=False)
    flag_type: Mapped[str] = mapped_column("flagType", String(50), nullable=False)
    severity: Mapped[str] = mapped_column("severity", String(20), nullable=False)

    agency = relationship("Agency", lazy="noload")
