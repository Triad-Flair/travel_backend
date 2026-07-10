import logging

import httpx

logger = logging.getLogger(__name__)


async def lookup_ifsc(code: str) -> dict:
    """Razorpay's public IFSC lookup — free, no API key. Not CORS-enabled,
    so this must be called server-side and proxied to the frontend."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://ifsc.razorpay.com/{code}")
            if resp.status_code != 200:
                return {"valid": False}
            data = resp.json()
            return {
                "valid": True,
                "bank": data.get("BANK"),
                "branch": data.get("BRANCH"),
                "address": data.get("ADDRESS"),
                "city": data.get("CITY"),
                "state": data.get("STATE"),
                "district": data.get("DISTRICT"),
            }
    except Exception as exc:
        logger.error("IFSC lookup failed for %s: %s", code, exc)
        return {"valid": False, "error": str(exc)}
