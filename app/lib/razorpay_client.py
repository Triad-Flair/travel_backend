import hashlib
import hmac
import logging

from app.config import settings
from app.exceptions import PaymentError

logger = logging.getLogger(__name__)


def _get_client():
    try:
        import razorpay  # lazy — razorpay SDK needs pkg_resources
        return razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))
    except ImportError as exc:
        raise PaymentError("Razorpay SDK not available") from exc


def create_order(amount_paise: int, receipt: str, notes: dict | None = None) -> dict:
    try:
        return _get_client().order.create({
            "amount": amount_paise,
            "currency": "INR",
            "receipt": receipt,
            "notes": notes or {},
        })
    except PaymentError:
        raise
    except Exception as exc:
        logger.error("Razorpay order creation failed: %s", exc)
        raise PaymentError("Failed to create payment order")


def verify_signature(order_id: str, payment_id: str, signature: str) -> bool:
    message = f"{order_id}|{payment_id}"
    expected = hmac.new(
        settings.razorpay_key_secret.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(body: bytes, signature: str) -> bool:
    expected = hmac.new(
        settings.razorpay_webhook_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def capture_payment(payment_id: str, amount_paise: int) -> dict:
    try:
        return _get_client().payment.capture(payment_id, amount_paise, {"currency": "INR"})
    except PaymentError:
        raise
    except Exception as exc:
        logger.error("Razorpay capture failed for %s: %s", payment_id, exc)
        raise PaymentError("Payment capture failed")


def create_transfer(payment_id: str, account_id: str, amount_paise: int, notes: dict | None = None) -> dict:
    try:
        return _get_client().payment.transfer(payment_id, {
            "transfers": [{
                "account": account_id,
                "amount": amount_paise,
                "currency": "INR",
                "notes": notes or {},
            }]
        })
    except PaymentError:
        raise
    except Exception as exc:
        logger.error("Razorpay transfer failed: %s", exc)
        raise PaymentError("Fund transfer failed")


def refund_payment(payment_id: str, amount_paise: int, notes: dict | None = None) -> dict:
    try:
        return _get_client().payment.refund(payment_id, {
            "amount": amount_paise,
            "notes": notes or {},
        })
    except PaymentError:
        raise
    except Exception as exc:
        logger.error("Razorpay refund failed for %s: %s", payment_id, exc)
        raise PaymentError("Refund failed")
