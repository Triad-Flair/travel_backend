import logging
from pathlib import Path

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import settings

logger = logging.getLogger(__name__)

_jinja_env: Environment | None = None


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
        msg["From"] = settings.smtp_user
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


async def send_welcome_email(to: str, name: str) -> None:
    html = f"""
    <html><body>
    <h1>Welcome to {settings.app_name}, {name}!</h1>
    <p>Your account has been created successfully.</p>
    <p>Start planning your next adventure today.</p>
    </body></html>
    """
    await send_email(to, f"Welcome to {settings.app_name}!", html)


async def send_verification_email(to: str, name: str, token: str) -> None:
    verify_url = f"{settings.frontend_url}/verify-email?token={token}"
    html = f"""
    <html><body>
    <h1>Verify Your Email</h1>
    <p>Hi {name}, please verify your email address by clicking the link below:</p>
    <a href="{verify_url}" style="background:#4F46E5;color:white;padding:12px 24px;
    text-decoration:none;border-radius:6px;display:inline-block;">Verify Email</a>
    <p>This link expires in 24 hours.</p>
    </body></html>
    """
    await send_email(to, "Verify your email address", html)


async def send_offer_notification_email(to: str, plan_title: str, agency_name: str) -> None:
    html = f"""
    <html><body>
    <h1>New Offer on Your Plan!</h1>
    <p>{agency_name} has submitted an offer for your trip: <strong>{plan_title}</strong></p>
    <p>Log in to review and respond to the offer.</p>
    </body></html>
    """
    await send_email(to, f"New offer on {plan_title}", html)
