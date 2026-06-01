from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    # App
    app_name: str = "Travellers India"
    environment: Literal["development", "staging", "production", "test"] = "development"
    debug: bool = False
    port: int = 4010
    frontend_url: str = "http://localhost:3000"
    api_prefix: str = "/api/v1"
    allowed_origins: str = "http://localhost:3000"

    # Database
    database_url: str
    sync_database_url: str = ""

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # JWT
    jwt_access_secret: str
    jwt_refresh_secret: str
    jwt_access_expire_minutes: int = 15
    jwt_refresh_expire_days: int = 30
    jwt_algorithm: str = "HS256"

    # Rate Limiting
    rate_limit_per_minute: int = 100
    auth_rate_limit_per_minute: int = 30

    # AWS S3
    s3_bucket: str = ""
    s3_region: str = "ap-south-1"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_endpoint: str = ""

    # Razorpay
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    # Email
    zeptomail_api_url: str = ""
    zeptomail_api_key: str = ""
    zeptomail_from_address: str = "noreply@travellersindia.com"
    zeptomail_from_name: str = "Travellers India"
    smtp_host: str = "smtp.zoho.com"
    smtp_port: int = 465
    smtp_secure: bool = True
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_test_secret: str = ""

    # WhatsApp
    msg91_auth_key: str = ""
    msg91_template_id: str = ""
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_api_version: str = "v22.0"

    # GST
    gstincheck_api_key: str = ""

    # DigiLocker
    digilocker_verify_url: str = ""
    digilocker_client_id: str = ""
    digilocker_client_secret: str = ""

    # Encryption
    encryption_key: str = ""

    # Celery
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    @field_validator("jwt_access_secret", "jwt_refresh_secret")
    @classmethod
    def validate_secret_length(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("JWT secret must be at least 32 characters")
        return v

    @field_validator("debug", mode="before")
    @classmethod
    def coerce_debug(cls, v):
        if isinstance(v, bool):
            return v
        if v is None:
            return False
        if isinstance(v, str):
            normalized = v.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off", ""}:
                return False
            # Tolerate environments where DEBUG is set to log levels.
            if normalized in {"warn", "warning", "info", "error", "critical"}:
                return False
            if normalized == "debug":
                return True
        return False

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_development(self) -> bool:
        return self.environment == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
