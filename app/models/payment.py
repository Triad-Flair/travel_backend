from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from datetime import datetime

from sqlalchemy import ForeignKey, Boolean, DateTime, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel, TimestampsMixin


class Payment(TimestampsMixin, BaseModel):
    __tablename__ = "payments"
    __table_args__ = {"extend_existing": True}

    user_id: Mapped[str] = mapped_column("userId", String(36), ForeignKey("users.id"), nullable=False, index=True)
    group_id: Mapped[str] = mapped_column("groupId", String(36), ForeignKey("groups.id"), nullable=False, index=True)
    agency_id: Mapped[str | None] = mapped_column("agencyId", String(36), nullable=True)
    plan_id: Mapped[str | None] = mapped_column("planId", String(36), nullable=True)
    package_id: Mapped[str | None] = mapped_column("packageId", String(36), nullable=True)
    amount: Mapped[int] = mapped_column("amount", Integer, nullable=False)
    currency: Mapped[str] = mapped_column("currency", String(3), default="INR", nullable=False)
    razorpay_order_id: Mapped[str | None] = mapped_column("razorpayOrderId", String(100), nullable=True)
    razorpay_payment_id: Mapped[str | None] = mapped_column("razorpayPaymentId", String(100), nullable=True)
    status: Mapped[str] = mapped_column("status", PgEnum("PENDING","AUTHORIZED","CAPTURED","REFUNDED","FAILED",name="PaymentStatus",create_type=False), default="PENDING", nullable=False, index=True)
    escrow_status: Mapped[str] = mapped_column("escrowStatus", PgEnum("HELD","PARTIAL_RELEASE","RELEASED","REFUNDED",name="EscrowStatus",create_type=False), default="HELD", nullable=False)
    # Replaces the old tranche1Released/tranche2Released pair — the agency's
    # net share now goes out in one Razorpay Route transfer at confirmation
    # rather than split 45/55 across confirmation and trip completion.
    # payoutAmountPaise tracks the actual amount ever transferred (not just
    # a boolean) so a partially-paid-out payment from the old scheme can be
    # topped up to its full amount exactly once, never double-paid.
    payout_amount_paise: Mapped[int] = mapped_column("payoutAmountPaise", Integer, default=0, nullable=False)
    payout_released: Mapped[bool] = mapped_column("payoutReleased", Boolean, default=False, nullable=False)
    trip_amount: Mapped[int | None] = mapped_column("tripAmount", Integer, nullable=True)
    platform_fee_amount: Mapped[int | None] = mapped_column("platformFeeAmount", Integer, nullable=True)
    fee_gst_amount: Mapped[int | None] = mapped_column("feeGstAmount", Integer, nullable=True)
    commission_amount: Mapped[int | None] = mapped_column("commissionAmount", Integer, nullable=True)
    source: Mapped[str | None] = mapped_column("source", PgEnum("PLAN_OFFER","PACKAGE",name="PaymentSource",create_type=False), nullable=True)
    transfer_status: Mapped[str] = mapped_column("transferStatus", PgEnum("QUEUED","PROCESSING","SETTLED","FAILED","MANUAL",name="TransferStatus",create_type=False), default="QUEUED", nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column("paidAt", DateTime(timezone=True), nullable=True)
    points_redeemed: Mapped[int] = mapped_column("pointsRedeemed", Integer, default=0, nullable=False)
    wallet_amount_used: Mapped[int] = mapped_column("walletAmountUsed", Integer, default=0, nullable=False)
    payout_frozen: Mapped[bool] = mapped_column("payoutFrozen", Boolean, default=False, nullable=False)
    promo_code: Mapped[str | None] = mapped_column("promoCode", String(50), nullable=True)
    promo_discount_amount: Mapped[int] = mapped_column("promoDiscountAmount", Integer, default=0, nullable=False)

    group = relationship("Group", back_populates="payments")
    user = relationship("User", lazy="noload")
    invoice = relationship("Invoice", back_populates="payment", uselist=False, lazy="noload")


class Invoice(TimestampsMixin, BaseModel):
    __tablename__ = "invoices"
    __table_args__ = {"extend_existing": True}

    group_id: Mapped[str] = mapped_column("groupId", String(36), nullable=False)
    payment_id: Mapped[str] = mapped_column("paymentId", String(36), ForeignKey("payments.id"), nullable=False, unique=True, index=True)
    agency_id: Mapped[str] = mapped_column("agencyId", String(36), nullable=False)
    user_id: Mapped[str] = mapped_column("userId", String(36), nullable=False, index=True)
    invoice_number: Mapped[str] = mapped_column("invoiceNumber", String(50), nullable=False, unique=True)
    amount: Mapped[int] = mapped_column("amount", Integer, nullable=False)
    platform_fee_amount: Mapped[int] = mapped_column("platformFeeAmount", Integer, default=0, nullable=False)
    fee_gst_amount: Mapped[int] = mapped_column("feeGstAmount", Integer, default=0, nullable=False)
    commission_amount: Mapped[int] = mapped_column("commissionAmount", Integer, default=0, nullable=False)
    total_amount: Mapped[int] = mapped_column("totalAmount", Integer, nullable=False)
    currency: Mapped[str] = mapped_column("currency", String(3), default="INR", nullable=False)
    status: Mapped[str] = mapped_column("status", String(20), default="PENDING", nullable=False)
    pdf_url: Mapped[str | None] = mapped_column("pdfUrl", Text, nullable=True)
    # Generated PDFs stored directly in Postgres (bytea) rather than object
    # storage — no S3/Supabase Storage credentials are configured, and these
    # are small per-invoice documents, not bulk media. Two separate blobs
    # because the user-payment and agency-settlement invoices are genuinely
    # different documents with different audiences (see schemas/invoices.py
    # — the settlement one carries commission data the user one must never see).
    user_pdf_data: Mapped[bytes | None] = mapped_column("userPdfData", LargeBinary, nullable=True)
    agency_pdf_data: Mapped[bytes | None] = mapped_column("agencyPdfData", LargeBinary, nullable=True)
    pdf_generated_at: Mapped[datetime | None] = mapped_column("pdfGeneratedAt", DateTime(timezone=True), nullable=True)

    payment = relationship("Payment", back_populates="invoice")


class PromotionalDiscount(TimestampsMixin, BaseModel):
    __tablename__ = "promotional_discounts"
    __table_args__ = {"extend_existing": True}
    code: Mapped[str] = mapped_column("code", String(50), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column("isActive", Boolean, default=True, nullable=False)
    description: Mapped[str | None] = mapped_column("description", String(255), nullable=True)
    discount_type: Mapped[str] = mapped_column(
        "discountType", String(20), nullable=False, default="PERCENTAGE"
    )
    discount_value: Mapped[int] = mapped_column("discountValue", Integer, nullable=False, default=0)
    max_discount_paise: Mapped[int | None] = mapped_column("maxDiscountPaise", Integer, nullable=True)
    min_order_amount_paise: Mapped[int | None] = mapped_column("minOrderAmountPaise", Integer, nullable=True)
    usage_limit: Mapped[int | None] = mapped_column("usageLimit", Integer, nullable=True)
    per_user_limit: Mapped[int | None] = mapped_column("perUserLimit", Integer, nullable=True, default=1)
    expires_at: Mapped[datetime | None] = mapped_column("expiresAt", DateTime(timezone=True), nullable=True)


class PromoCodeUsage(BaseModel):
    """Pre-existing table from before the FastAPI/SQLAlchemy port — no
    createdAt/updatedAt, tracks usedAt/discountApplied instead. Confirmed
    against the live schema (information_schema.columns) after the
    TimestampsMixin-based version of this model crashed _finalize_capture
    with asyncpg.exceptions.UndefinedColumnError: column "updatedAt" of
    relation "promo_code_usages" does not exist."""
    __tablename__ = "promo_code_usages"
    __table_args__ = {"extend_existing": True}
    promo_id: Mapped[str] = mapped_column("promoId", String(36), nullable=False)
    user_id: Mapped[str] = mapped_column("userId", String(36), nullable=False)
    payment_id: Mapped[str | None] = mapped_column("paymentId", String(36), ForeignKey("payments.id"), nullable=True)
    discount_applied: Mapped[int] = mapped_column("discountApplied", Integer, nullable=False, default=0)
    used_at: Mapped[datetime] = mapped_column("usedAt", DateTime(timezone=False), nullable=False, default=datetime.utcnow)


class GstVerificationLog(TimestampsMixin, BaseModel):
    __tablename__ = "gst_verification_logs"
    __table_args__ = {"extend_existing": True}
    gstin: Mapped[str] = mapped_column("gstin", String(15), nullable=False)
    status: Mapped[str] = mapped_column("status", String(20), nullable=False)


class Dispute(TimestampsMixin, BaseModel):
    __tablename__ = "disputes"
    __table_args__ = {"extend_existing": True}
    payment_id: Mapped[str] = mapped_column("paymentId", String(36), ForeignKey("payments.id"), nullable=False)
    reason: Mapped[str] = mapped_column("reason", Text, nullable=False)
    status: Mapped[str] = mapped_column("status", String(20), default="OPEN", nullable=False)
    # CUSTOMER: filed by a traveler via /payments/disputes (create_dispute) —
    # a support ticket, not tied to Razorpay. RAZORPAY_CHARGEBACK: created
    # from a payment.dispute.* webhook — a real bank-initiated chargeback
    # that can claw back settled funds. These are different concepts that
    # happen to share a table; source is how callers tell them apart.
    source: Mapped[str] = mapped_column("source", String(20), default="CUSTOMER", nullable=False)
    razorpay_dispute_id: Mapped[str | None] = mapped_column("razorpayDisputeId", String(100), nullable=True)
    payment = relationship("Payment", lazy="noload")
