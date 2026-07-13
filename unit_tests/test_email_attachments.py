"""send_email had no attachment parameter at all before this — every
invoice email was HTML-only, with no way to attach the generated PDF.
Confirms the SMTP and ZeptoMail paths both actually construct a real
attachment rather than silently dropping it.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.lib.email import EmailAttachment, send_email


@pytest.mark.asyncio
async def test_smtp_send_without_attachments_is_multipart_alternative():
    captured = {}

    async def _fake_send(msg, **kwargs):
        captured["msg"] = msg

    with patch("app.lib.email.settings") as mock_settings, patch("aiosmtplib.send", new=_fake_send):
        mock_settings.zeptomail_api_key = ""
        mock_settings.smtp_user = "noreply@example.com"
        mock_settings.smtp_host = "smtp.example.com"
        mock_settings.smtp_port = 465
        mock_settings.smtp_password = "secret"
        mock_settings.smtp_secure = True
        mock_settings.zeptomail_from_name = "TravellersIn"
        ok = await send_email("traveler@example.com", "Subject", "<p>Body</p>")

    assert ok is True
    assert captured["msg"].get_content_type() == "multipart/alternative"


@pytest.mark.asyncio
async def test_smtp_send_with_attachment_is_multipart_mixed_and_carries_the_file():
    captured = {}

    async def _fake_send(msg, **kwargs):
        captured["msg"] = msg

    attachment = EmailAttachment("TSU-2026-ABCD1234.pdf", b"%PDF-1.4 fake pdf bytes", "application/pdf")

    with patch("app.lib.email.settings") as mock_settings, patch("aiosmtplib.send", new=_fake_send):
        mock_settings.zeptomail_api_key = ""
        mock_settings.smtp_user = "noreply@example.com"
        mock_settings.smtp_host = "smtp.example.com"
        mock_settings.smtp_port = 465
        mock_settings.smtp_password = "secret"
        mock_settings.smtp_secure = True
        mock_settings.zeptomail_from_name = "TravellersIn"
        ok = await send_email("traveler@example.com", "Subject", "<p>Body</p>", [attachment])

    assert ok is True
    msg = captured["msg"]
    assert msg.get_content_type() == "multipart/mixed"

    attachment_parts = [
        part for part in msg.walk()
        if part.get_content_disposition() == "attachment"
    ]
    assert len(attachment_parts) == 1
    assert attachment_parts[0].get_filename() == "TSU-2026-ABCD1234.pdf"
    assert attachment_parts[0].get_payload(decode=True) == b"%PDF-1.4 fake pdf bytes"


@pytest.mark.asyncio
async def test_zeptomail_send_encodes_attachment_as_base64():
    captured = {}

    class _FakeResponse:
        def raise_for_status(self):
            pass

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers, json, timeout):
            captured["json"] = json
            return _FakeResponse()

    attachment = EmailAttachment("TSA-2026-ABCD1234.pdf", b"agency pdf bytes", "application/pdf")

    with patch("app.lib.email.settings") as mock_settings, patch("httpx.AsyncClient", return_value=_FakeClient()):
        mock_settings.zeptomail_api_key = "zk_test_key"
        mock_settings.zeptomail_api_url = "https://api.zeptomail.example/send"
        mock_settings.zeptomail_from_address = "noreply@example.com"
        mock_settings.zeptomail_from_name = "TravellersIn"
        ok = await send_email("agency@example.com", "Subject", "<p>Body</p>", [attachment])

    assert ok is True
    assert len(captured["json"]["attachments"]) == 1
    sent_attachment = captured["json"]["attachments"][0]
    assert sent_attachment["name"] == "TSA-2026-ABCD1234.pdf"
    assert sent_attachment["mime_type"] == "application/pdf"
    import base64
    assert base64.b64decode(sent_attachment["content"]) == b"agency pdf bytes"


@pytest.mark.asyncio
async def test_send_email_without_attachments_omits_attachments_key_for_zeptomail():
    captured = {}

    class _FakeResponse:
        def raise_for_status(self):
            pass

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers, json, timeout):
            captured["json"] = json
            return _FakeResponse()

    with patch("app.lib.email.settings") as mock_settings, patch("httpx.AsyncClient", return_value=_FakeClient()):
        mock_settings.zeptomail_api_key = "zk_test_key"
        mock_settings.zeptomail_api_url = "https://api.zeptomail.example/send"
        mock_settings.zeptomail_from_address = "noreply@example.com"
        mock_settings.zeptomail_from_name = "TravellersIn"
        await send_email("agency@example.com", "Subject", "<p>Body</p>")

    assert "attachments" not in captured["json"]
