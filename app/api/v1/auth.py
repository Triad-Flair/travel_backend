from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import CurrentUser, get_current_user
from app.lib.email import send_email
from app.schemas.auth import (
    AadhaarVerificationRequest,
    AadhaarVerificationResponse,
    AgencySignupRequest,
    AuthActionResponse,
    AuthSessionResponse,
    ForgotPasswordRequest,
    LoginRequest,
    RefreshRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    SignupMessageResponse,
    TravelerSignupRequest,
    UpdateProfileRequest,
    UserInSession,
    VerifyEmailResponse,
    VerificationStatusResponse,
)
from app.services import auth as auth_svc

router = APIRouter(prefix="/auth", tags=["auth"])


def _normalize_secret_candidates(raw_secret: str) -> list[str]:
    cleaned = raw_secret.strip()
    if not cleaned:
        return []
    candidates = {cleaned, cleaned.replace(" ", "+")}
    try:
        decoded = unquote(cleaned)
        candidates.add(decoded)
        candidates.add(decoded.replace(" ", "+"))
    except Exception:
        pass
    return list(candidates)


@router.post("/signup/traveler", response_model=SignupMessageResponse, status_code=201)
async def signup_traveler(req: TravelerSignupRequest, db: AsyncSession = Depends(get_db)):
    return await auth_svc.signup_traveler(db, req)


@router.post("/signup/agency", response_model=SignupMessageResponse, status_code=201)
async def signup_agency(req: AgencySignupRequest, db: AsyncSession = Depends(get_db)):
    return await auth_svc.signup_agency_owner(db, req)


@router.post("/login", response_model=AuthSessionResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    return await auth_svc.login(db, req)


@router.post("/refresh", response_model=AuthSessionResponse)
async def refresh(req: RefreshRequest, db: AsyncSession = Depends(get_db)):
    return await auth_svc.refresh_tokens(db, req.refresh_token)


@router.get("/me", response_model=UserInSession)
async def get_me(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await auth_svc.get_me(db, current_user.user_id)


@router.patch("/me", response_model=UserInSession)
async def update_me(
    req: UpdateProfileRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await auth_svc.update_profile(db, current_user.user_id, req)


@router.get("/me/verification", response_model=VerificationStatusResponse)
async def verification_status(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = await auth_svc.get_me(db, current_user.user_id)
    return VerificationStatusResponse(
        tier=user.verification or "BASIC",
        has_aadhaar=bool((await auth_svc.get_user_aadhaar_hash(db, current_user.user_id))),
        completed_trips=user.completed_trips,
        avg_rating=user.avg_rating,
        trusted_eligible=(user.completed_trips >= 3 and user.avg_rating >= 4.0),
    )


@router.post("/me/verification/aadhaar", response_model=AadhaarVerificationResponse)
async def verify_aadhaar(
    req: AadhaarVerificationRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await auth_svc.verify_aadhaar_for_user(db, current_user.user_id, req)


@router.get("/verify-email", response_model=VerifyEmailResponse)
async def verify_email(
    vt: str | None = None,
    token: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    token_to_verify = (vt or token or "").strip()
    return await auth_svc.verify_email(db, token_to_verify)


@router.post("/resend-verification", response_model=AuthActionResponse)
async def resend_verification(req: ResendVerificationRequest, db: AsyncSession = Depends(get_db)):
    return await auth_svc.resend_verification(db, req)


@router.post("/forgot-password", response_model=AuthActionResponse)
async def forgot_password(req: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    return await auth_svc.request_password_reset(db, req)


@router.post("/reset-password", response_model=SignupMessageResponse)
async def reset_password(req: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    return await auth_svc.reset_password(db, req)


@router.get("/smtp-test")
async def smtp_test_email(
    to: str,
    secret: str | None = None,
    header_secret: str | None = Header(default=None, alias="x-smtp-test-secret"),
):
    expected_secret = settings.smtp_test_secret.strip()
    if not expected_secret:
        raise HTTPException(status_code=500, detail="SMTP_TEST_SECRET is not configured on server")

    provided_secret = (header_secret or secret or "").strip()
    if expected_secret not in _normalize_secret_candidates(provided_secret):
        raise HTTPException(status_code=403, detail="Forbidden")

    if "@" not in to:
        raise HTTPException(status_code=400, detail="Provide a valid ?to= email address")

    sent = await send_email(
        to=to,
        subject=f"[{settings.app_name}] SMTP Test Email",
        html=(
            "<p>SMTP is working.</p>"
            f"<p>Host: <strong>{settings.smtp_host}</strong></p>"
            f"<p>From: <strong>{settings.smtp_user}</strong></p>"
        ),
    )
    if not sent:
        raise HTTPException(status_code=500, detail="SMTP send failed")
    return {"ok": True, "message": f"Test email sent to {to}"}
