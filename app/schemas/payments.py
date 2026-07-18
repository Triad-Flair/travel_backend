from datetime import datetime

from app.schemas.base import CamelModel


class CreateOrderRequest(CamelModel):
    points_to_redeem: int | None = 0
    wallet_amount_to_use: int | None = 0
    promo_code: str | None = None


class VerifyPaymentRequest(CamelModel):
    payment_id: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class MockCaptureRequest(CamelModel):
    payment_id: str


class ValidatePromoRequest(CamelModel):
    code: str
    group_id: str


class ValidatePromoResponse(CamelModel):
    valid: bool
    discount_type: str | None = None
    discount_value: int | None = None
    discount_paise: int | None = None
    message: str


class CreatePromoCodeRequest(CamelModel):
    code: str
    discount_type: str
    discount_value: int
    description: str | None = None
    max_discount_paise: int | None = None
    min_order_amount_paise: int | None = None
    usage_limit: int | None = None
    per_user_limit: int | None = 1
    expires_at: datetime | None = None


class PromoCodeResponse(CamelModel):
    id: str
    code: str
    is_active: bool
    description: str | None
    discount_type: str
    discount_value: int
    max_discount_paise: int | None
    min_order_amount_paise: int | None
    usage_limit: int | None
    per_user_limit: int | None
    expires_at: datetime | None
    times_used: int
    total_discount_given_paise: int
    created_at: datetime


class PaymentRecordResponse(CamelModel):
    id: str
    user_id: str
    group_id: str
    amount: int
    currency: str
    razorpay_order_id: str | None = None
    razorpay_payment_id: str | None = None
    status: str
    escrow_status: str
    payout_released: bool
    payout_frozen: bool = False
    points_redeemed: int = 0
    wallet_amount_used: int | str = 0
    promo_code: str | None = None
    promo_discount_amount: int = 0
    trip_amount: int | None = None
    platform_fee_amount: int | None = None
    fee_gst_amount: int | None = None
    commission_amount: int | None = None
    created_at: str
    updated_at: str


class GroupPaymentStateResponse(CamelModel):
    group_id: str
    agency_name: str
    payment_source: str
    plan: dict | None = None
    package: dict | None = None
    offer: dict | None = None
    payment: PaymentRecordResponse | None = None
    amount: int
    breakdown: dict
    loyalty: dict | None = None
    wallet: dict | None = None
    currency: str
    committed_count: int
    traveler_count: int
    checkout_mode: str
    razorpay_key_id: str | None = None


class GroupPaymentOrderResponse(CamelModel):
    payment: PaymentRecordResponse
    amount: int
    breakdown: dict
    payment_source: str
    currency: str
    checkout_mode: str
    razorpay_key_id: str | None = None
    description: str | None = None


class AgencyWalletSummary(CamelModel):
    pending_balance: int
    available_balance: int
    total_earned: int
    total_commission: int
    security_deposit: int
    payout_mode: str


class AgencyWalletTransaction(CamelModel):
    id: str
    type: str
    amount: int
    description: str | None = None
    group_id: str | None = None
    payment_id: str | None = None
    razorpay_transfer_id: str | None = None
    created_at: str


class CreateDisputeRequest(CamelModel):
    payment_id: str
    reason: str


class DisputeResponse(CamelModel):
    id: str
    payment_id: str
    reason: str
    status: str
    source: str = "CUSTOMER"
    razorpay_dispute_id: str | None = None
    created_at: str


class InvoiceResponse(CamelModel):
    id: str
    invoice_number: str
    total_amount: int
    currency: str
    status: str
    created_at: str
