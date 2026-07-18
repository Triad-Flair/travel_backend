from typing import Literal

from pydantic import Field

from app.schemas.base import CamelModel

AgencyOperationalStatus = Literal["PENDING", "APPROVED", "REJECTED", "PAUSED", "SUSPENDED"]


class AgencyProfile(CamelModel):
    id: str
    name: str
    slug: str
    description: str | None = None
    logo_url: str | None = None
    phone: str | None = None
    email: str | None = None
    city: str | None = None
    state: str | None = None
    address: str | None = None
    postal_code: str | None = None
    gstin: str | None = None
    pan: str | None = None
    tourism_license: str | None = None
    specializations: list[str] | None = None
    destinations: list[str] | None = None
    verification_status: str = "pending"
    gstin_verified: bool = False
    avg_rating: float = 0.0
    review_count: int = 0
    total_trips: int = 0
    created_at: str = ""


class CreateAgencyRequest(CamelModel):
    name: str = Field(..., min_length=2, max_length=200)
    description: str | None = None
    city: str | None = None
    state: str | None = None
    address: str | None = None
    postal_code: str | None = None
    phone: str | None = None
    email: str | None = None
    gstin: str | None = None
    pan: str | None = None
    tourism_license: str | None = None
    specializations: list[str] = []
    destinations: list[str] = []


class UpdateAgencyRequest(CamelModel):
    name: str | None = None
    description: str | None = None
    logo_url: str | None = None
    city: str | None = None
    state: str | None = None
    address: str | None = None
    postal_code: str | None = None
    phone: str | None = None
    email: str | None = None
    gstin: str | None = None
    pan: str | None = None
    tourism_license: str | None = None
    specializations: list[str] | None = None
    destinations: list[str] | None = None


class GstVerifyResponse(CamelModel):
    gstin: str
    valid: bool
    legal_name: str | None = None
    trade_name: str | None = None
    registration_date: str | None = None
    status: str | None = None


class IfscLookupResponse(CamelModel):
    ifsc: str
    valid: bool
    bank: str | None = None
    branch: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    district: str | None = None


class AgencyMemberResponse(CamelModel):
    id: str
    role: str
    is_active: bool
    created_at: str
    user: dict | None = None


class InviteMemberRequest(CamelModel):
    user_id: str
    role: str = "AGENT"


class AgencyCustomerRequest(CamelModel):
    name: str
    email: str | None = None
    phone: str | None = None
    notes: str | None = None
    user_id: str | None = None


class KycUploadUrlRequest(CamelModel):
    document_type: str
    filename: str
    content_type: str


class InsuranceQuoteRequest(CamelModel):
    provider: str
    premium_amount: int
    coverage_amount: int
    pax_count: int = Field(default=1, ge=1)
    start_date: str
    end_date: str
    group_id: str | None = None


class AgencyVerificationFlags(CamelModel):
    name: bool
    email: bool
    phone: bool
    bank_details: bool
    gst: bool
    pan: bool
    travel_license: bool


class AgencyBankDetailsBrief(CamelModel):
    """Masked — the same convention get_bank_record already uses (never the
    raw account number)."""
    account_holder_name: str | None = None
    bank_name: str | None = None
    masked_account_number: str | None = None
    ifsc_code: str | None = None


class AgencyAdminDirectoryItem(CamelModel):
    """One row of the Super Admin Dashboard's agency directory — the brief
    details PRD calls for (name/email/phone/bankDetails/gst/pan/
    travelLicense) plus the operational status and per-field verification
    checklist that drives the review drawer."""
    id: str
    name: str
    slug: str
    email: str | None = None
    phone: str | None = None
    gstin: str | None = None
    pan: str | None = None
    tourism_license: str | None = None
    bank_details: AgencyBankDetailsBrief | None = None
    status: AgencyOperationalStatus
    verification_flags: AgencyVerificationFlags
    all_flags_verified: bool
    created_at: str


class UpdateVerificationFlagsRequest(CamelModel):
    """Partial — only the flags present in the request body are changed,
    everything else on the agency's checklist is left as-is."""
    name: bool | None = None
    email: bool | None = None
    phone: bool | None = None
    bank_details: bool | None = None
    gst: bool | None = None
    pan: bool | None = None
    travel_license: bool | None = None


class UpdateAgencyStatusRequest(CamelModel):
    status: AgencyOperationalStatus
    reason: str | None = None
