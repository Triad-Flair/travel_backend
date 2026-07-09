from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from datetime import datetime

from sqlalchemy import ForeignKey, Boolean, DateTime, Integer, String, Text
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
    tranche1_released: Mapped[bool] = mapped_column("tranche1Released", Boolean, default=False, nullable=False)
    tranche2_released: Mapped[bool] = mapped_column("tranche2Released", Boolean, default=False, nullable=False)
    trip_amount: Mapped[int | None] = mapped_column("tripAmount", Integer, nullable=True)
    platform_fee_amount: Mapped[int | None] = mapped_column("platformFeeAmount", Integer, nullable=True)
    fee_gst_amount: Mapped[int | None] = mapped_column("feeGstAmount", Integer, nullable=True)
    commission_amount: Mapped[int | None] = mapped_column("commissionAmount", Integer, nullable=True)
    source: Mapped[str | None] = mapped_column("source", PgEnum("PLAN_OFFER","PACKAGE",name="PaymentSource",create_type=False), nullable=True)
    transfer_status: Mapped[str] = mapped_column("transferStatus", PgEnum("QUEUED","PROCESSING","SETTLED","FAILED","MANUAL",name="TransferStatus",create_type=False), default="QUEUED", nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column("paidAt", DateTime(timezone=True), nullable=True)
    points_redeemed: Mapped[int] = mapped_column("pointsRedeemed", Integer, default=0, nullable=False)
    wallet_amount_used: Mapped[int] = mapped_column("walletAmountUsed", Integer, default=0, nullable=False)

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

    payment = relationship("Payment", back_populates="invoice")


class PromotionalDiscount(TimestampsMixin, BaseModel):
    __tablename__ = "promotional_discounts"
    __table_args__ = {"extend_existing": True}
    code: Mapped[str] = mapped_column("code", String(50), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column("isActive", Boolean, default=True, nullable=False)


class PromoCodeUsage(TimestampsMixin, BaseModel):
    __tablename__ = "promo_code_usages"
    __table_args__ = {"extend_existing": True}
    promo_id: Mapped[str] = mapped_column("promoId", String(36), nullable=False)
    user_id: Mapped[str] = mapped_column("userId", String(36), nullable=False)
    payment_id: Mapped[str] = mapped_column("paymentId", String(36), ForeignKey("payments.id"), nullable=False)


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
    payment = relationship("Payment", lazy="noload")
