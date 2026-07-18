from app.schemas.base import CamelModel


class PlatformInfo(CamelModel):
    name: str
    # Customer-facing brand shown prominently in the invoice header/logo —
    # deliberately separate from `name`, which stays the exact registered
    # legal entity name required on every GST line/signature/declaration.
    brand_name: str
    tagline: str | None = None
    address: str
    gstin: str
    email: str
    website: str
    # LLPIN, not CIN — Triad Flair IT Solutions LLP is an LLP, which is
    # registered with an LLP Identification Number, not a Company
    # Identification Number (that's only for Pvt Ltd/Ltd entities).
    llpin: str
    support_email: str
    support_phone: str | None = None


class InvoiceTravelerInfo(CamelModel):
    name: str
    email: str
    phone: str | None = None
    city: str | None = None


class InvoiceAgencyInfo(CamelModel):
    """Shown on both invoice types — the agency's own GSTIN/PAN on an invoice
    addressed to a specific counterparty is normal invoicing practice, not a
    public-listing leak (see AgencyPublicSummary in schemas/common.py for
    that separate, already-handled concern)."""
    name: str
    gstin: str
    pan: str
    address: str
    email: str
    phone: str
    owner_name: str | None = None
    logo_url: str | None = None


class InvoiceClientInfo(CamelModel):
    name: str
    email: str
    phone: str | None = None


class InvoiceTripInfo(CamelModel):
    title: str
    destination: str
    start_date: str | None = None
    end_date: str | None = None
    traveler_count: int
    price_per_person: int
    inclusions: list = []
    cancellation_policy: str
    plan_type: str = "STANDARD"
    accommodation: str | None = None
    vibes: list = []


class SettlementTripInfo(CamelModel):
    title: str
    destination: str
    start_date: str | None = None
    end_date: str | None = None
    traveler_count: int


class InvoiceLineItem(CamelModel):
    description: str
    subtext: str
    qty: int
    unit: str
    rate: int
    subtotal: int


class InvoiceDiscountLine(CamelModel):
    label: str
    amount: int


class InvoiceSummary(CamelModel):
    """User-facing total only — deliberately has no commission/split fields.
    See test_invoice_schemas.py for the guard test."""
    subtotal: int
    trip_amount: int
    platform_fee: int
    gst_on_platform_fee: int
    discount_lines: list[InvoiceDiscountLine] = []
    total_discounts: int
    grand_total: int


class UserPaymentInfo(CamelModel):
    """No escrowSchedule here — that reveals the agency's payout split
    (tranche amounts derived from agencyNetAmount = tripAmount - commission),
    which combined with tripAmount elsewhere on this same invoice would let a
    traveler back-calculate the platform's commission. Settlement/schedule
    info belongs only on AgencySettlementResponse."""
    id: str
    razorpay_order_id: str | None = None
    razorpay_payment_id: str | None = None
    currency: str
    paid_at: str | None = None


class UserInvoiceResponse(CamelModel):
    invoice_type: str = "USER_PAYMENT"
    invoice_number: str
    issued_at: str | None = None
    status: str
    escrow_status: str
    platform: PlatformInfo
    traveler: InvoiceTravelerInfo
    agency: InvoiceAgencyInfo | None = None
    trip: InvoiceTripInfo
    line_items: list[InvoiceLineItem]
    summary: InvoiceSummary
    payment: UserPaymentInfo
    points_redeemed: int = 0
    wallet_amount_used: int = 0
    refund_policy: list[dict] = []
    terms_and_conditions: list[str] = []
    members: list[str] = []


class PayoutScheduleItem(CamelModel):
    tranche: int
    label: str
    amount: int
    released: bool
    expected_date: str | None = None


class SettlementInfo(CamelModel):
    """Agency-facing only — the gross/commission/net split. Never expose this
    shape (or its field names) on UserInvoiceResponse."""
    total_collected: int
    trip_amount: int
    platform_commission: int
    platform_fee: int
    gst_on_fee: int
    agency_net_amount: int
    schedule: list[PayoutScheduleItem]


class SettlementPaymentInfo(CamelModel):
    id: str
    razorpay_order_id: str | None = None
    razorpay_payment_id: str | None = None
    currency: str
    paid_at: str | None = None
    transfer_reference: str | None = None


class AgencySettlementResponse(CamelModel):
    invoice_type: str = "AGENCY_SETTLEMENT"
    invoice_number: str
    issued_at: str | None = None
    status: str
    transfer_status: str
    platform: PlatformInfo
    agency: InvoiceAgencyInfo
    client: InvoiceClientInfo
    trip: SettlementTripInfo
    settlement: SettlementInfo
    payment: SettlementPaymentInfo
    members: list[str] = []
    terms_and_conditions: list[str] = []
