import json

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_SKIP_PATHS = {"/health", "/docs", "/redoc", "/openapi.json"}
_SKIP_PREFIXES = ("/docs", "/redoc", "/openapi")


class EnvelopeMiddleware(BaseHTTPMiddleware):
    """Wraps JSON responses:
      - 2xx  →  { "data": <original body> }
      - 4xx/5xx  →  { "errors": [{ "code": ..., "message": ... }] }
    Leaves non-JSON and WebSocket/SSE responses untouched.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip static paths (docs, health, WS upgrades)
        path = request.url.path
        if path in _SKIP_PATHS or any(path.startswith(p) for p in _SKIP_PREFIXES):
            return await call_next(request)
        if request.headers.get("upgrade", "").lower() == "websocket":
            return await call_next(request)

        response = await call_next(request)

        ct = response.headers.get("content-type", "")
        if "application/json" not in ct:
            return response

        # Read body
        body_bytes = b""
        async for chunk in response.body_iterator:
            body_bytes += chunk

        try:
            original = json.loads(body_bytes)
        except Exception:
            return Response(
                content=body_bytes,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=ct,
            )

        status = response.status_code

        if 200 <= status < 300:
            # Already wrapped (e.g. health check returns its own dict) — skip if has "data" key
            if isinstance(original, dict) and "data" in original:
                wrapped = original
            else:
                wrapped = {"data": original}
        else:
            # Normalize error into { errors: [{ code, message }] }
            if isinstance(original, dict) and "error" in original:
                err = original["error"]
                wrapped = {
                    "errors": [{"code": err.get("code", "ERROR"), "message": err.get("message", "An error occurred")}]
                }
            elif isinstance(original, dict) and "detail" in original:
                detail = original["detail"]
                if isinstance(detail, list):
                    wrapped = {
                        "errors": [{"code": "VALIDATION_ERROR", "message": str(d.get("msg", d))} for d in detail]
                    }
                else:
                    wrapped = {"errors": [{"code": "ERROR", "message": str(detail)}]}
            elif isinstance(original, dict) and "errors" in original:
                wrapped = original
            else:
                wrapped = {"errors": [{"code": "ERROR", "message": "An error occurred"}]}

        new_body = json.dumps(wrapped).encode()
        headers = dict(response.headers)
        headers["content-length"] = str(len(new_body))

        return Response(
            content=new_body,
            status_code=status,
            headers=headers,
            media_type="application/json",
        )
