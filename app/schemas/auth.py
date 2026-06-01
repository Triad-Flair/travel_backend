from pydantic import EmailStr, Field

from app.schemas.base import CamelModel


# ── Request schemas ───────────────────────────────────────────────────────────

class TravelerSignupRequest(CamelModel):
    full_name: str = Field(..., min_length=2, max_length=100)
    username: str = Field(..., min_length=3, max_length=50, pattern=r"^[a-z0-9_]+$")
    email: EmailStr | None = None
    phone: str = Field(..., min_length=10, max_length=15)
    password: str = Field(..., min_length=8)
    date_of_birth: str | None = None
    gender: str | None = None
    city: str | None = None
    travel_preferences: str | None = None
    bio: str | None = None
    avatar_url: str | None = None
    referral_code: str | None = None


class AgencySignupRequest(CamelModel):
    full_name: str = Field(..., min_length=2, max_length=100)
    username: str = Field(..., min_length=3, max_length=50, pattern=r"^[a-z0-9_]+$")
    email: EmailStr
    phone: str | None = None
    password: str = Field(..., min_length=8)
    date_of_birth: str | None = None
    gender: str | None = None
    city: str | None = None
    travel_preferences: str | None = None
    bio: str | None = None
    avatar_url: str | None = None
    # Agency-specific
    agency_name: str = Field(..., min_length=2, max_length=200)
    agency_description: str | None = None
    agency_phone: str | None = None
    agency_email: str | None = None
    agency_address: str | None = None
    agency_city: str | None = None
    agency_state: str | None = None
    gstin: str | None = None
    pan: str | None = None
    tourism_license: str | None = None
    specializations: list[str] = []
    destinations: list[str] = []


class LoginRequest(CamelModel):
    identifier: str = Field(..., description="Email, phone, or username")
    password: str


class RefreshRequest(CamelModel):
    refresh_token: str


# ── Response schemas ──────────────────────────────────────────────────────────

class AgencySummaryInSession(CamelModel):
    id: str
    name: str
    slug: str
    logo_url: str | None = None
    verification: str | None = None


class UserInSession(CamelModel):
    """UserProfile shape the frontend's AuthSession.user expects."""
    id: str
    full_name: str
    username: str | None = None
    avatar_url: str | None = None
    phone: str | None = None
    email: str | None = None
    bio: str | None = None
    date_of_birth: str | None = None
    gender: str | None = None
    city: str | None = None
    travel_preferences: str | None = None
    referral_code: str | None = None
    verification: str | None = None
    avg_rating: float = 0.0
    completed_trips: int = 0
    agency: AgencySummaryInSession | None = None


class AuthSessionResponse(CamelModel):
    """Matches frontend AuthSession exactly."""
    access_token: str
    refresh_token: str
    user: UserInSession
    agency_id: str | None = None
    role: str


class SignupMessageResponse(CamelModel):
    message: str


class UpdateProfileRequest(CamelModel):
    full_name: str | None = Field(None, min_length=2, max_length=100)
    email: EmailStr | None = None
    bio: str | None = Field(None, max_length=500)
    avatar_url: str | None = None
    city: str | None = None
    travel_preferences: str | None = None
    gender: str | None = None
    date_of_birth: str | None = None


class VerificationStatusResponse(CamelModel):
    tier: str
    has_aadhaar: bool
    completed_trips: int
    avg_rating: float
    trusted_eligible: bool


class ResendVerificationRequest(CamelModel):
    email: EmailStr


class AadhaarVerificationRequest(CamelModel):
    full_name: str = Field(..., min_length=2, max_length=100)
    aadhaar_number: str = Field(..., min_length=12, max_length=12, pattern=r"^\d{12}$")
    date_of_birth: str | None = None
    consent: bool = True


class AadhaarVerificationResponse(CamelModel):
    tier: str
    provider: str
    masked_aadhaar: str
    full_name: str
    date_of_birth: str | None = None
    agency: AgencySummaryInSession | None = None


class VerifyEmailResponse(CamelModel):
    message: str
