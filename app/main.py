import logging
import time
from contextlib import asynccontextmanager

import socketio
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy.exc import IntegrityError

from app.api.v1.router import v1_router
from app.config import settings
from app.exceptions import AppError
from app.middleware.envelope import EnvelopeMiddleware
from app.redis_client import close_redis, get_redis
from app.websockets.socketio_server import sio, socket_asgi_app

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s [%s]", settings.app_name, settings.environment)
    redis = await get_redis()
    logger.info("Redis: %s", "connected" if redis else "unavailable — caching disabled")
    yield
    await close_redis()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="2.0.0",
        description="TripSync Travel Marketplace API — FastAPI",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ── Rate limiting ────────────────────────────────────────────────────────
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # ── CORS — must be before EnvelopeMiddleware ──────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Response envelope: wraps all JSON in { data: T } ─────────────────────
    app.add_middleware(EnvelopeMiddleware)

    # ── Request timing ────────────────────────────────────────────────────────
    @app.middleware("http")
    async def add_process_time(request: Request, call_next):
        t = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - t) * 1000:.1f}"
        return response

    # ── Exception handlers ────────────────────────────────────────────────────
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        # Return in `errors` array format so EnvelopeMiddleware picks it up
        return JSONResponse(
            status_code=exc.status_code,
            content={"errors": [{"code": exc.code, "message": exc.message}]},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        errors = [
            {"code": "VALIDATION_ERROR", "message": f"{'.'.join(str(l) for l in e['loc'][1:])}: {e['msg']}"}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"errors": errors},
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(request: Request, exc: IntegrityError):
        logger.warning("Integrity error %s %s: %s", request.method, request.url, exc)
        detail = str(getattr(exc, "orig", exc)).lower()

        if "duplicate key value violates unique constraint" in detail or "unique constraint failed" in detail:
            if "phone" in detail:
                message = "Phone already registered"
            elif "email" in detail:
                message = "Email already registered"
            elif "username" in detail:
                message = "Username already taken"
            elif "gstin" in detail:
                message = "GSTIN is already registered"
            else:
                message = "A record with the same unique value already exists"

            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={"errors": [{"code": "CONFLICT", "message": message}]},
            )

        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "errors": [
                    {
                        "code": "DB_CONSTRAINT_ERROR",
                        "message": "The request could not be saved because it violates a database constraint.",
                    }
                ]
            },
        )

    @app.exception_handler(Exception)
    async def generic_handler(request: Request, exc: Exception):
        logger.exception("Unhandled error %s %s", request.method, request.url)
        msg = str(exc) if settings.debug else "Internal server error"
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"errors": [{"code": "INTERNAL_ERROR", "message": msg}]},
        )

    # ── Routes ────────────────────────────────────────────────────────────────
    app.include_router(v1_router)

    @app.get("/health", tags=["health"])
    async def health():
        redis = await get_redis()
        return {
            "status": "ok",
            "app": settings.app_name,
            "environment": settings.environment,
            "redis": "connected" if redis else "unavailable",
        }

    return app


# ── Build FastAPI app ─────────────────────────────────────────────────────────
_fastapi_app = create_app()

# ── Mount Socket.IO at /socket.io so frontend `io(SOCKET_URL)` connects ──────
# Uses socketio.ASGIApp which wraps FastAPI for non-socket.io requests
app = socketio.ASGIApp(
    sio,
    other_asgi_app=_fastapi_app,
    socketio_path="socket.io",
)
