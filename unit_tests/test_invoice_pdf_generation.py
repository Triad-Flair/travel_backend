"""ensure_invoice_pdfs orchestrates PDF generation at payment-capture time.
Must be idempotent (re-running after a partial failure only fills the gap,
and repeat Celery task retries don't re-render for nothing) and must never
let a rendering failure propagate — a broken PDF must not block payment
capture, which is the actual business-critical event.

Every test here mocks render_user_invoice_pdf/render_agency_settlement_pdf
directly — this suite must never depend on WeasyPrint's system libraries
(libpango/libcairo) being installed, since most dev machines won't have them.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.invoices import ensure_invoice_pdfs


def _fake_payment(**overrides):
    defaults = dict(id="payment-1", group_id="group-1", user_id="user-1", agency_id="agency-1")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_invoice(**overrides):
    defaults = dict(id="invoice-1", user_pdf_data=None, agency_pdf_data=None, pdf_generated_at=None)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_generates_both_pdfs_when_agency_present():
    payment = _fake_payment()
    invoice = _fake_invoice()
    db = AsyncMock()

    with patch("app.services.invoices._ensure_invoice", new=AsyncMock(return_value=invoice)), \
         patch("app.services.invoices._build_user_invoice_data", new=AsyncMock(return_value="user_payload")), \
         patch("app.services.invoices._build_agency_settlement_data", new=AsyncMock(return_value="agency_payload")), \
         patch("app.services.invoices._trip_context", new=AsyncMock(return_value={"agency": object()})), \
         patch("app.services.invoices.render_user_invoice_pdf", return_value=b"user-pdf-bytes") as mock_user_render, \
         patch("app.services.invoices.render_agency_settlement_pdf", return_value=b"agency-pdf-bytes") as mock_agency_render:
        result = await ensure_invoice_pdfs(db, payment)

    mock_user_render.assert_called_once_with("user_payload")
    mock_agency_render.assert_called_once_with("agency_payload")
    assert result.user_pdf_data == b"user-pdf-bytes"
    assert result.agency_pdf_data == b"agency-pdf-bytes"
    assert result.pdf_generated_at is not None


@pytest.mark.asyncio
async def test_skips_agency_pdf_when_no_agency():
    payment = _fake_payment()
    invoice = _fake_invoice()
    db = AsyncMock()

    with patch("app.services.invoices._ensure_invoice", new=AsyncMock(return_value=invoice)), \
         patch("app.services.invoices._build_user_invoice_data", new=AsyncMock(return_value="user_payload")), \
         patch("app.services.invoices._trip_context", new=AsyncMock(return_value={"agency": None})), \
         patch("app.services.invoices.render_user_invoice_pdf", return_value=b"user-pdf-bytes"), \
         patch("app.services.invoices.render_agency_settlement_pdf") as mock_agency_render:
        result = await ensure_invoice_pdfs(db, payment)

    mock_agency_render.assert_not_called()
    assert result.user_pdf_data == b"user-pdf-bytes"
    assert result.agency_pdf_data is None


@pytest.mark.asyncio
async def test_idempotent_skips_already_generated_pdfs():
    payment = _fake_payment()
    invoice = _fake_invoice(user_pdf_data=b"already-there", agency_pdf_data=b"already-there-too")
    db = AsyncMock()

    with patch("app.services.invoices._ensure_invoice", new=AsyncMock(return_value=invoice)), \
         patch("app.services.invoices.render_user_invoice_pdf") as mock_user_render, \
         patch("app.services.invoices.render_agency_settlement_pdf") as mock_agency_render:
        result = await ensure_invoice_pdfs(db, payment)

    mock_user_render.assert_not_called()
    mock_agency_render.assert_not_called()
    assert result.pdf_generated_at is None  # nothing regenerated, nothing to timestamp
    db.flush.assert_not_called()


@pytest.mark.asyncio
async def test_user_pdf_failure_does_not_block_agency_pdf_or_raise():
    payment = _fake_payment()
    invoice = _fake_invoice()
    db = AsyncMock()

    with patch("app.services.invoices._ensure_invoice", new=AsyncMock(return_value=invoice)), \
         patch("app.services.invoices._build_user_invoice_data", new=AsyncMock(side_effect=Exception("db blew up"))), \
         patch("app.services.invoices._build_agency_settlement_data", new=AsyncMock(return_value="agency_payload")), \
         patch("app.services.invoices._trip_context", new=AsyncMock(return_value={"agency": object()})), \
         patch("app.services.invoices.render_agency_settlement_pdf", return_value=b"agency-pdf-bytes"):
        result = await ensure_invoice_pdfs(db, payment)

    # Must not raise — a broken PDF render must never block payment capture.
    assert result.user_pdf_data is None
    assert result.agency_pdf_data == b"agency-pdf-bytes"


@pytest.mark.asyncio
async def test_agency_pdf_failure_does_not_block_user_pdf_or_raise():
    payment = _fake_payment()
    invoice = _fake_invoice()
    db = AsyncMock()

    with patch("app.services.invoices._ensure_invoice", new=AsyncMock(return_value=invoice)), \
         patch("app.services.invoices._build_user_invoice_data", new=AsyncMock(return_value="user_payload")), \
         patch("app.services.invoices.render_user_invoice_pdf", return_value=b"user-pdf-bytes"), \
         patch("app.services.invoices._trip_context", new=AsyncMock(side_effect=Exception("trip context blew up"))):
        result = await ensure_invoice_pdfs(db, payment)

    assert result.user_pdf_data == b"user-pdf-bytes"
    assert result.agency_pdf_data is None
