"""get_user_invoice_pdf/get_agency_settlement_pdf — the download endpoints
behind the "Download PDF" buttons. Must enforce the same ownership checks as
the existing JSON invoice endpoints, and generate on-demand (via
ensure_invoice_pdfs) for a payment captured before this feature existed
rather than 404ing outright.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.exceptions import ForbiddenError, NotFoundError
from app.services.invoices import get_agency_settlement_pdf, get_user_invoice_pdf


def _fake_payment(**overrides):
    defaults = dict(id="payment-1", user_id="user-1")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_invoice(**overrides):
    defaults = dict(invoice_number="TSU-2026-ABCD1234", user_pdf_data=b"pdf-bytes", agency_pdf_data=b"agency-pdf-bytes")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_get_user_invoice_pdf_rejects_wrong_owner():
    payment = _fake_payment(user_id="user-1")
    db = AsyncMock()

    with patch("app.services.invoices._resolve_payment", new=AsyncMock(return_value=payment)):
        with pytest.raises(ForbiddenError):
            await get_user_invoice_pdf(db, "payment-1", "someone-else")


@pytest.mark.asyncio
async def test_get_user_invoice_pdf_returns_bytes_and_number():
    payment = _fake_payment(user_id="user-1")
    invoice = _fake_invoice()
    db = AsyncMock()

    with patch("app.services.invoices._resolve_payment", new=AsyncMock(return_value=payment)), \
         patch("app.services.invoices.ensure_invoice_pdfs", new=AsyncMock(return_value=invoice)):
        pdf_bytes, invoice_number = await get_user_invoice_pdf(db, "payment-1", "user-1")

    assert pdf_bytes == b"pdf-bytes"
    assert invoice_number == "TSU-2026-ABCD1234"


@pytest.mark.asyncio
async def test_get_user_invoice_pdf_raises_not_found_if_generation_failed():
    payment = _fake_payment(user_id="user-1")
    invoice = _fake_invoice(user_pdf_data=None)
    db = AsyncMock()

    with patch("app.services.invoices._resolve_payment", new=AsyncMock(return_value=payment)), \
         patch("app.services.invoices.ensure_invoice_pdfs", new=AsyncMock(return_value=invoice)):
        with pytest.raises(NotFoundError):
            await get_user_invoice_pdf(db, "payment-1", "user-1")


@pytest.mark.asyncio
async def test_get_agency_settlement_pdf_rejects_non_owner():
    payment = _fake_payment()
    agency = SimpleNamespace(owner_id="owner-1")
    db = AsyncMock()

    with patch("app.services.invoices._resolve_payment", new=AsyncMock(return_value=payment)), \
         patch("app.services.invoices._trip_context", new=AsyncMock(return_value={"agency": agency})):
        with pytest.raises(ForbiddenError):
            await get_agency_settlement_pdf(db, "payment-1", "not-the-owner")


@pytest.mark.asyncio
async def test_get_agency_settlement_pdf_renames_invoice_number_prefix():
    payment = _fake_payment()
    agency = SimpleNamespace(owner_id="owner-1")
    invoice = _fake_invoice(invoice_number="TSU-2026-ABCD1234")
    db = AsyncMock()

    with patch("app.services.invoices._resolve_payment", new=AsyncMock(return_value=payment)), \
         patch("app.services.invoices._trip_context", new=AsyncMock(return_value={"agency": agency})), \
         patch("app.services.invoices.ensure_invoice_pdfs", new=AsyncMock(return_value=invoice)):
        pdf_bytes, invoice_number = await get_agency_settlement_pdf(db, "payment-1", "owner-1")

    assert pdf_bytes == b"agency-pdf-bytes"
    assert invoice_number == "TSA-2026-ABCD1234"
