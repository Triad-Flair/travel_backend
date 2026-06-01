import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def verify_gstin(gstin: str) -> dict:
    if not settings.gstincheck_api_key:
        return {"valid": False, "error": "GST verification not configured"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://api.gstincheck.co.in/v1/checkgst/{gstin}",
                headers={"apikey": settings.gstincheck_api_key},
            )
            data = resp.json()
            if data.get("flag"):
                return {
                    "valid": True,
                    "legal_name": data.get("data", {}).get("lgnm"),
                    "trade_name": data.get("data", {}).get("tradeNam"),
                    "registration_date": data.get("data", {}).get("rgdt"),
                    "status": data.get("data", {}).get("sts"),
                    "raw": data,
                }
            return {"valid": False, "raw": data}
    except Exception as exc:
        logger.error("GST verification failed for %s: %s", gstin, exc)
        return {"valid": False, "error": str(exc)}
