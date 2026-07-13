"""Razorpay Route Linked Account automation.

This is the v2 Accounts API (marketplace/linked-account onboarding), a
different surface from the v1 Orders/Payments API in razorpay_client.py —
there's no SDK resource class for it in razorpay==2.0.1, so this talks to
Razorpay directly over HTTP with the same key_id/key_secret Basic Auth used
everywhere else.

category/subcategory ("tours_and_travel"/"travel_agency") and the required
request shape were confirmed empirically against the live account (Route was
enabled 2026-07-13) — Razorpay's own docs don't publish the full enum table,
so guessing wrong here would silently create malformed accounts.
"""
import logging

import httpx

from app.config import settings
from app.exceptions import PaymentError

logger = logging.getLogger(__name__)

ACCOUNTS_BASE_URL = "https://api.razorpay.com/v2/accounts"


def _auth() -> tuple[str, str]:
    return (settings.razorpay_key_id, settings.razorpay_key_secret)


async def create_linked_account(
    *,
    email: str,
    phone: str,
    legal_business_name: str,
    contact_name: str,
    reference_id: str,
    street1: str,
    street2: str,
    city: str,
    state: str,
    postal_code: str,
) -> dict:
    payload = {
        "email": email,
        "phone": phone,
        "type": "route",
        "reference_id": reference_id,
        "legal_business_name": legal_business_name,
        # Every agency on this platform is, by definition, a travel/tour
        # business — "not_yet_registered" is what Razorpay itself defaults
        # to when no more specific legal entity type is supplied, which is
        # honest here since we don't collect proprietorship/pvt-ltd/etc.
        "business_type": "not_yet_registered",
        "contact_name": contact_name,
        "profile": {
            "category": "tours_and_travel",
            "subcategory": "travel_agency",
            "addresses": {
                "registered": {
                    "street1": street1,
                    "street2": street2,
                    "city": city,
                    "state": state,
                    "postal_code": postal_code,
                    "country": "IN",
                }
            },
        },
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(ACCOUNTS_BASE_URL, json=payload, auth=_auth())
    except Exception as exc:
        logger.error("Razorpay linked account request errored: %s", exc)
        raise PaymentError("Failed to reach Razorpay to create the linked account") from exc

    if resp.status_code >= 400:
        logger.error("Razorpay linked account creation failed (%s): %s", resp.status_code, resp.text)
        detail = resp.json().get("error", {}).get("description", "unknown error") if resp.content else "unknown error"
        raise PaymentError(f"Razorpay rejected the linked account: {detail}")

    return resp.json()


async def configure_route_settlement(
    account_id: str,
    *,
    account_number: str,
    ifsc_code: str,
    beneficiary_name: str,
) -> dict:
    """Requesting the "route" product is idempotent — calling it again on an
    account that already has one returns the same product id rather than
    creating a duplicate, confirmed empirically — so this can run on every
    bank-detail update without tracking a separate product id ourselves."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            create_resp = await client.post(
                f"{ACCOUNTS_BASE_URL}/{account_id}/products",
                json={"product_name": "route", "tnc_accepted": True},
                auth=_auth(),
            )
            if create_resp.status_code >= 400:
                logger.error("Razorpay route product request failed (%s): %s", create_resp.status_code, create_resp.text)
                raise PaymentError("Failed to request the Razorpay route product")

            product_id = create_resp.json()["id"]

            patch_resp = await client.patch(
                f"{ACCOUNTS_BASE_URL}/{account_id}/products/{product_id}",
                json={
                    "settlements": {
                        "account_number": account_number,
                        "ifsc_code": ifsc_code,
                        "beneficiary_name": beneficiary_name,
                    }
                },
                auth=_auth(),
            )
    except PaymentError:
        raise
    except Exception as exc:
        logger.error("Razorpay route settlement config errored: %s", exc)
        raise PaymentError("Failed to configure Razorpay settlement details") from exc

    if patch_resp.status_code >= 400:
        logger.error("Razorpay route settlement config failed (%s): %s", patch_resp.status_code, patch_resp.text)
        raise PaymentError("Failed to configure Razorpay settlement details")

    return patch_resp.json()
