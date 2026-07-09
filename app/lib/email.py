import logging
from pathlib import Path

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import settings

logger = logging.getLogger(__name__)

_jinja_env: Environment | None = None

# Always an absolute, publicly reachable URL — email clients cannot load
# assets from localhost or from whatever FRONTEND_URL is set to in dev.
LOGO_URL = "https://travellersin.com/_next/image?url=%2Fbrand%2Ftravellersin.png&w=640&q=75"

# Brand tokens, kept in sync with frontend/app/globals.css
COLOR_INK = "#1a1a2e"
COLOR_INK_SOFT = "#4a4e69"
COLOR_MUTED = "#9a9cb8"
COLOR_SURFACE = "#f6f8fb"
COLOR_BORDER = "#eef1f6"
COLOR_PRIMARY = "#0d9670"
COLOR_PRIMARY_DARK = "#0a7a5c"


def _get_jinja() -> Environment:
    global _jinja_env
    if _jinja_env is None:
        templates_dir = Path(__file__).parent / "email_templates"
        templates_dir.mkdir(exist_ok=True)
        _jinja_env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape(["html"]),
        )
    return _jinja_env


def _format_inr(amount_paise: int | None) -> str:
    amount = max(0, int(amount_paise or 0)) / 100
    return f"Rs. {amount:,.2f}"


def _button(label: str, url: str) -> str:
    return f"""
    <table role="presentation" cellpadding="0" cellspacing="0" style="margin:24px 0 4px;">
      <tr>
        <td style="border-radius:999px;background:{COLOR_PRIMARY};">
          <a href="{url}" style="display:inline-block;padding:13px 28px;font-size:14px;font-weight:600;
          color:#ffffff;text-decoration:none;border-radius:999px;font-family:-apple-system,BlinkMacSystemFont,
          'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">{label}</a>
        </td>
      </tr>
    </table>
    """


def _feature_list(items: list[tuple[str, str]]) -> str:
    rows = "".join(
        f"""
        <tr>
          <td style="padding:10px 0;border-bottom:1px solid {COLOR_BORDER};">
            <p style="margin:0;font-size:14px;font-weight:600;color:{COLOR_INK};">{title}</p>
            <p style="margin:2px 0 0;font-size:13px;color:{COLOR_INK_SOFT};line-height:1.5;">{desc}</p>
          </td>
        </tr>
        """
        for title, desc in items
    )
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:16px 0;">{rows}</table>'


def _shell(preheader: str, body_html: str) -> str:
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{settings.app_name}</title>
</head>
<body style="margin:0;padding:0;background:{COLOR_SURFACE};font-family:-apple-system,BlinkMacSystemFont,
'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{COLOR_SURFACE};padding:32px 16px;">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;background:#ffffff;
border-radius:16px;overflow:hidden;box-shadow:0 1px 3px rgba(26,26,46,0.08);">
<tr>
<td style="padding:24px 32px;border-bottom:1px solid {COLOR_BORDER};">
<img src="{LOGO_URL}" width="132" height="41" alt="{settings.app_name}" style="display:block;border:0;">
</td>
</tr>
<tr>
<td style="padding:32px;color:{COLOR_INK_SOFT};font-size:14px;line-height:1.6;">
{body_html}
</td>
</tr>
<tr>
<td style="padding:20px 32px 28px;border-top:1px solid {COLOR_BORDER};color:{COLOR_MUTED};font-size:12px;line-height:1.5;">
<p style="margin:0 0 4px;">{settings.app_name} · India</p>
<p style="margin:0;">This is an automated message — please don't reply directly to this email.</p>
</td>
</tr>
</table>
</td></tr>
</table>
</body>
</html>"""


async def send_email(to: str, subject: str, html: str) -> bool:
    if settings.zeptomail_api_key:
        return await _send_via_zeptomail(to, subject, html)
    return await _send_via_smtp(to, subject, html)


async def _send_via_zeptomail(to: str, subject: str, html: str) -> bool:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                settings.zeptomail_api_url,
                headers={
                    "Authorization": settings.zeptomail_api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "from": {
                        "address": settings.zeptomail_from_address,
                        "name": settings.zeptomail_from_name,
                    },
                    "to": [{"email_address": {"address": to}}],
                    "subject": subject,
                    "htmlbody": html,
                },
                timeout=10,
            )
            resp.raise_for_status()
            return True
    except Exception as exc:
        logger.error("ZeptoMail send failed to %s: %s", to, exc)
        return False


async def _send_via_smtp(to: str, subject: str, html: str) -> bool:
    try:
        import aiosmtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{settings.zeptomail_from_name} <{settings.smtp_user}>"
        msg["To"] = to
        msg.attach(MIMEText(html, "html"))

        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_password,
            use_tls=settings.smtp_secure,
        )
        return True
    except Exception as exc:
        logger.error("SMTP send failed to %s: %s", to, exc)
        return False


async def send_verification_email(to: str, name: str, token: str) -> bool:
    verify_url = f"{settings.frontend_url}/verify-email?token={token}"
    body = f"""
    <h1 style="margin:0 0 12px;font-size:20px;font-weight:700;color:{COLOR_INK};">Confirm your email address</h1>
    <p style="margin:0 0 4px;">Hi {name},</p>
    <p style="margin:0;">Thanks for creating a {settings.app_name} account. Please confirm this is your email
    address to activate your account.</p>
    {_button("Verify email address", verify_url)}
    <p style="margin:16px 0 0;font-size:13px;color:{COLOR_MUTED};">
    This link expires in {settings.email_verification_expire_hours} hours. If you didn't create this account,
    you can safely ignore this email.</p>
    """
    return await send_email(to, f"Verify your email to activate your {settings.app_name} account", _shell("Confirm your email to activate your account.", body))


async def send_traveler_welcome_email(to: str, name: str) -> bool:
    explore_url = f"{settings.frontend_url}/discover"
    body = f"""
    <h1 style="margin:0 0 12px;font-size:20px;font-weight:700;color:{COLOR_INK};">Your account is ready</h1>
    <p style="margin:0;">Hi {name}, your email is verified and your {settings.app_name} account is now active.</p>
    {_feature_list([
        ("Plan a trip", "Create an itinerary and invite friends to join as a group."),
        ("Discover destinations", "Browse curated plans and packages from across India and beyond."),
        ("Get agency offers", "Verified travel agencies can bid directly on your trip plans."),
        ("Track everything", "Bookings, payments, and invoices in one dashboard."),
    ])}
    {_button("Start exploring", explore_url)}
    """
    return await send_email(to, f"Welcome to {settings.app_name}, {name}", _shell("Your account is verified and ready to go.", body))


async def send_agency_welcome_email(to: str, name: str, agency_name: str) -> bool:
    dashboard_url = f"{settings.frontend_url}/agency/dashboard"
    body = f"""
    <h1 style="margin:0 0 12px;font-size:20px;font-weight:700;color:{COLOR_INK};">Your agency is live</h1>
    <p style="margin:0;">Hi {name}, your email is verified and <strong>{agency_name}</strong> is now listed
    on {settings.app_name}.</p>
    {_feature_list([
        ("Verify your GSTIN &amp; PAN", "Unlock a trust badge that travelers can see on your listing."),
        ("Create trip offers", "Bid on traveler plans that match what your agency offers."),
        ("Manage bookings", "Track invoices, settlements, and payouts in one place."),
        ("Grow your reach", "Get discovered by travelers actively planning trips."),
    ])}
    {_button("Go to agency dashboard", dashboard_url)}
    """
    return await send_email(to, f"{agency_name} is live on {settings.app_name}", _shell(f"{agency_name} is now live on {settings.app_name}.", body))


async def send_password_reset_email(to: str, name: str, token: str) -> bool:
    reset_url = f"{settings.frontend_url}/reset-password?token={token}"
    body = f"""
    <h1 style="margin:0 0 12px;font-size:20px;font-weight:700;color:{COLOR_INK};">Reset your password</h1>
    <p style="margin:0;">Hi {name}, we received a request to reset the password on your {settings.app_name} account.</p>
    {_button("Reset password", reset_url)}
    <p style="margin:16px 0 0;font-size:13px;color:{COLOR_MUTED};">
    This link expires shortly. If you didn't request this, you can safely ignore this email — your password
    will not be changed.</p>
    """
    return await send_email(to, "Reset your password", _shell("Reset the password on your account.", body))


async def send_payment_receipt_email(
    to: str,
    name: str,
    invoice_number: str,
    trip_title: str,
    invoice_url: str,
    total_amount_paise: int,
) -> bool:
    body = f"""
    <h1 style="margin:0 0 12px;font-size:20px;font-weight:700;color:{COLOR_INK};">Payment confirmed</h1>
    <p style="margin:0;">Hi {name}, your payment for <strong>{trip_title}</strong> has been captured successfully.</p>
    {_feature_list([
        ("Invoice", invoice_number),
        ("Total paid", _format_inr(total_amount_paise)),
    ])}
    {_button("View invoice", invoice_url)}
    """
    return await send_email(to, f"Payment confirmed for {trip_title}", _shell(f"Your payment for {trip_title} is confirmed.", body))


async def send_agency_booking_invoice_email(
    to: str,
    owner_name: str,
    agency_name: str,
    invoice_number: str,
    trip_title: str,
    settlement_url: str,
    total_amount_paise: int,
) -> bool:
    body = f"""
    <h1 style="margin:0 0 12px;font-size:20px;font-weight:700;color:{COLOR_INK};">New traveler payment captured</h1>
    <p style="margin:0;">Hi {owner_name}, a new payment has been captured for <strong>{trip_title}</strong>
    under {agency_name}.</p>
    {_feature_list([
        ("Settlement ref", invoice_number),
        ("Collected amount", _format_inr(total_amount_paise)),
    ])}
    {_button("View settlement", settlement_url)}
    """
    return await send_email(to, f"New payment captured for {trip_title}", _shell(f"A new payment was captured for {trip_title}.", body))


async def send_agency_payout_update_email(
    to: str,
    owner_name: str,
    agency_name: str,
    invoice_number: str,
    tranche_label: str,
    escrow_status: str,
    settlement_url: str,
    released_amount_paise: int,
) -> bool:
    body = f"""
    <h1 style="margin:0 0 12px;font-size:20px;font-weight:700;color:{COLOR_INK};">Payout update for {agency_name}</h1>
    <p style="margin:0;">Hi {owner_name}, {tranche_label} has been released.</p>
    {_feature_list([
        ("Settlement ref", invoice_number),
        ("Released amount", _format_inr(released_amount_paise)),
        ("Escrow status", escrow_status),
    ])}
    {_button("Open settlement", settlement_url)}
    """
    return await send_email(to, f"{tranche_label} released", _shell(f"{tranche_label} has been released.", body))


async def send_offer_notification_email(to: str, plan_title: str, agency_name: str) -> bool:
    body = f"""
    <h1 style="margin:0 0 12px;font-size:20px;font-weight:700;color:{COLOR_INK};">New offer on your plan</h1>
    <p style="margin:0;"><strong>{agency_name}</strong> has submitted an offer for your trip:
    <strong>{plan_title}</strong>.</p>
    <p style="margin:12px 0 0;">Log in to {settings.app_name} to review and respond to the offer.</p>
    {_button("Review offer", settings.frontend_url)}
    """
    return await send_email(to, f"New offer on {plan_title}", _shell(f"{agency_name} sent you a new offer.", body))
