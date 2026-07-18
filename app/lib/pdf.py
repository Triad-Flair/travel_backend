"""Invoice PDF rendering — reuses the same HTML/CSS invoice design shown
on-screen, rendered server-side via WeasyPrint so it can be attached to
emails and downloaded as a real file (previously nothing generated an
actual PDF anywhere; the invoice pages only called window.print())."""
import base64
import logging
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from app.schemas.invoices import AgencySettlementResponse, UserInvoiceResponse

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "invoices"
_LOGO_PATH = Path(__file__).parent.parent / "assets" / "triad-flair-logo.png"
_BRAND_LOGO_PATH = Path(__file__).parent.parent / "assets" / "travellersin-logo.png"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)


def _data_uri(path: Path) -> str:
    """Embedded as base64 rather than referenced by path/URL — WeasyPrint
    runs in a container with no guarantee of network access at render time,
    and a local file:// path would need reproducing the exact container
    filesystem layout in every renderer invocation for no benefit."""
    try:
        data = path.read_bytes()
        return f"data:image/png;base64,{base64.b64encode(data).decode()}"
    except FileNotFoundError:
        logger.warning("Invoice logo not found at %s — rendering without it", path)
        return ""


@lru_cache(maxsize=1)
def _logo_data_uri() -> str:
    return _data_uri(_LOGO_PATH)


@lru_cache(maxsize=1)
def _brand_logo_data_uri() -> str:
    return _data_uri(_BRAND_LOGO_PATH)


def _inr(paise: int | None) -> str:
    amount = (paise or 0) / 100
    return f"{amount:,.2f}"


def _fmt_datetime(value: str | None) -> str | None:
    """Renders stored ISO timestamps as "16 Jul 2026, 1:26 PM" instead of the
    raw ISO string invoices were previously showing verbatim."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return dt.strftime("%d %b %Y, %I:%M %p").replace(" 0", " ")


def _fmt_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return dt.strftime("%d %b %Y")


def render_user_invoice_pdf(payload: UserInvoiceResponse) -> bytes:
    data = payload.model_dump(by_alias=False)
    trip = {**data["trip"], "start_date": _fmt_date(data["trip"]["start_date"]), "end_date": _fmt_date(data["trip"]["end_date"])}
    context = {
        "logo_data_uri": _logo_data_uri(),
        "brand_logo_data_uri": _brand_logo_data_uri(),
        "platform": data["platform"],
        "invoice_number": data["invoice_number"],
        "issued_at": _fmt_datetime(data["issued_at"]),
        "status": data["status"],
        "traveler": data["traveler"],
        "agency": data["agency"],
        "agency_name": data["agency"]["name"] if data["agency"] else None,
        "trip": trip,
        "line_items": [
            {
                "description": item["description"],
                "subtext": item["subtext"],
                "rate_inr": _inr(item["rate"]),
                "qty": item["qty"],
                "unit": item["unit"],
                "subtotal_inr": _inr(item["subtotal"]),
            }
            for item in data["line_items"]
        ],
        "discount_lines": [
            {"label": line["label"], "amount_inr": _inr(abs(line["amount"]))}
            for line in data["summary"]["discount_lines"]
        ],
        "grand_total_inr": _inr(data["summary"]["grand_total"]),
    }
    html = _env.get_template("user_invoice.html").render(**context)
    return HTML(string=html, base_url=str(_TEMPLATES_DIR)).write_pdf()


def render_agency_settlement_pdf(payload: AgencySettlementResponse) -> bytes:
    data = payload.model_dump(by_alias=False)
    settlement = data["settlement"]
    trip = {**data["trip"], "start_date": _fmt_date(data["trip"]["start_date"]), "end_date": _fmt_date(data["trip"]["end_date"])}
    context = {
        "logo_data_uri": _logo_data_uri(),
        "brand_logo_data_uri": _brand_logo_data_uri(),
        "platform": data["platform"],
        "invoice_number": data["invoice_number"],
        "issued_at": _fmt_datetime(data["issued_at"]),
        "transfer_status": data["transfer_status"],
        "agency": data["agency"],
        "agency_name": data["agency"]["name"],
        "client": data["client"],
        "trip": trip,
        "trip_amount_inr": _inr(settlement["trip_amount"]),
        "platform_commission_inr": _inr(settlement["platform_commission"]),
        # GST is strictly a traveler-platform matter (never deducted from or
        # owed to the agency) — deliberately not surfaced on this document at
        # all, unconditionally, regardless of its value.
        "agency_net_inr": _inr(settlement["agency_net_amount"]),
        "payout_released": settlement["payout_released"],
    }
    html = _env.get_template("agency_settlement.html").render(**context)
    return HTML(string=html, base_url=str(_TEMPLATES_DIR)).write_pdf()
