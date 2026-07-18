"""Guards the user/agency invoice split. If these fail, someone added a
commission/settlement field back onto UserInvoiceResponse (or one of its
nested models), or the two response types got merged back into one."""
from app.schemas.invoices import (
    AgencySettlementResponse,
    InvoiceSummary,
    SettlementInfo,
    UserInvoiceResponse,
    UserPaymentInfo,
)

SPLIT_FIELD_NAMES = {
    "commission_amount",
    "commission",
    "platform_commission",
    "agency_net_amount",
    "agency_net",
    "escrow_schedule",
    "schedule",
    "transfer_status",
    "transfer_reference",
}


def _all_field_names(model) -> set[str]:
    """Recursively collect field names across a Pydantic model and any
    nested CamelModel fields, so a split field hidden a level deep still
    gets caught."""
    names: set[str] = set()
    for field_name, field in model.model_fields.items():
        names.add(field_name)
        annotation = field.annotation
        args = getattr(annotation, "__args__", ())
        candidates = (annotation, *args)
        for candidate in candidates:
            if isinstance(candidate, type) and hasattr(candidate, "model_fields") and candidate is not model:
                names |= _all_field_names(candidate)
    return names


def test_user_invoice_has_no_split_fields_anywhere_in_its_schema():
    found = _all_field_names(UserInvoiceResponse) & SPLIT_FIELD_NAMES
    assert not found, f"UserInvoiceResponse (or a nested model) leaks split fields: {found}"


def test_user_invoice_summary_has_no_commission_field():
    assert "commission_amount" not in InvoiceSummary.model_fields
    assert "commission" not in InvoiceSummary.model_fields


def test_user_payment_info_has_no_escrow_schedule():
    assert "escrow_schedule" not in UserPaymentInfo.model_fields


def test_agency_settlement_response_still_has_the_split_fields():
    """Confirms the guard tests above are meaningful — the agency-facing
    schema is expected to carry this data."""
    assert "settlement" in AgencySettlementResponse.model_fields
    assert "platform_commission" in SettlementInfo.model_fields
    assert "agency_net_amount" in SettlementInfo.model_fields
    assert "payout_released" in SettlementInfo.model_fields


def test_user_and_agency_invoice_are_genuinely_separate_schema_classes():
    assert UserInvoiceResponse is not AgencySettlementResponse
