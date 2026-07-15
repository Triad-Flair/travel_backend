"""Invoice PDF rendering — reuses the same HTML/CSS invoice design shown
on-screen, rendered server-side via WeasyPrint so it can be attached to
emails and downloaded as a real file (previously nothing generated an
actual PDF anywhere; the invoice pages only called window.print())."""
import base64
import logging
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from app.schemas.invoices import AgencySettlementResponse, UserInvoiceResponse

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "invoices"
_LOGO_PATH = Path(__file__).parent.parent / "assets" / "triad-flair-logo.png"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)


@lru_cache(maxsize=1)
def _logo_data_uri() -> str:
    """Embedded as base64 rather than referenced by path/URL — WeasyPrint
    runs in a container with no guarantee of network access at render time,
    and a local file:// path would need reproducing the exact container
    filesystem layout in every renderer invocation for no benefit."""
    try:
        data = _LOGO_PATH.read_bytes()
        return f"data:image/png;base64,{base64.b64encode(data).decode()}"
    except FileNotFoundError:
        logger.warning("Invoice logo not found at %s — rendering without it", _LOGO_PATH)
        return ""


def _inr(paise: int | None) -> str:
    amount = (paise or 0) / 100
    return f"{amount:,.2f}"


def render_user_invoice_pdf(payload: UserInvoiceResponse) -> bytes:
    data = payload.model_dump(by_alias=False)
    context = {
        "logo_data_uri": _logo_data_uri(),
        "platform": data["platform"],
        "invoice_number": data["invoice_number"],
        "issued_at": data["issued_at"],
        "status": data["status"],
        "traveler": data["traveler"],
        "agency": data["agency"],
        "agency_name": data["agency"]["name"] if data["agency"] else None,
        "trip": data["trip"],
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
    context = {
        "logo_data_uri": _logo_data_uri(),
        "platform": data["platform"],
        "invoice_number": data["invoice_number"],
        "issued_at": data["issued_at"],
        "transfer_status": data["transfer_status"],
        "agency": data["agency"],
        "agency_name": data["agency"]["name"],
        "client": data["client"],
        "trip": data["trip"],
        "total_collected_inr": _inr(settlement["total_collected"]),
        "trip_amount_inr": _inr(settlement["trip_amount"]),
        "platform_commission_inr": _inr(settlement["platform_commission"]),
        "has_platform_fee": (settlement["platform_fee"] + settlement["gst_on_fee"]) > 0,
        "platform_fee_inr": _inr(settlement["platform_fee"]),
        "gst_on_fee_inr": _inr(settlement["gst_on_fee"]),
        "agency_net_inr": _inr(settlement["agency_net_amount"]),
        "schedule": [
            {
                "tranche": item["tranche"],
                "label": item["label"],
                "amount_inr": _inr(item["amount"]),
                "released": item["released"],
            }
            for item in settlement["schedule"]
        ],
    }
    html = _env.get_template("agency_settlement.html").render(**context)
    return HTML(string=html, base_url=str(_TEMPLATES_DIR)).write_pdf()
