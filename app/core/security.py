import hashlib
import hmac
import os
from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings
from app.exceptions import InvalidTokenError, TokenExpiredError

# bcrypt for new passwords
_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash new passwords with bcrypt."""
    return _pwd_ctx.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify password — supports Node.js scrypt format AND bcrypt."""
    if hashed.startswith("scrypt$"):
        return _verify_scrypt(plain, hashed)
    try:
        return _pwd_ctx.verify(plain, hashed)
    except Exception:
        return False


def _verify_scrypt(password: str, stored: str) -> bool:
    """Verify Node.js-style scrypt hash: scrypt$<salt_hex>$<dk_hex>"""
    parts = stored.split("$")
    if len(parts) != 3 or parts[0] != "scrypt":
        return False
    try:
        # Legacy Express used: crypto.scrypt(password, salt_hex_string, 64)
        # so salt is the literal hex string bytes (not bytes.fromhex).
        salt = parts[1].encode()
        expected = bytes.fromhex(parts[2])
        # Node.js crypto.scrypt defaults: N=16384, r=8, p=1, dkLen=64
        derived = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=64)
        return hmac.compare_digest(derived, expected)
    except Exception:
        return False


def create_access_token(payload: dict) -> str:
    data = payload.copy()
    data["exp"] = datetime.now(UTC) + timedelta(minutes=settings.jwt_access_expire_minutes)
    data["iat"] = datetime.now(UTC)
    data["type"] = "access"
    return jwt.encode(data, settings.jwt_access_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(payload: dict) -> str:
    data = payload.copy()
    data["exp"] = datetime.now(UTC) + timedelta(days=settings.jwt_refresh_expire_days)
    data["iat"] = datetime.now(UTC)
    data["type"] = "refresh"
    return jwt.encode(data, settings.jwt_refresh_secret, algorithm=settings.jwt_algorithm)


def create_action_token(payload: dict, *, purpose: str, expires_in: timedelta) -> str:
    data = payload.copy()
    now = datetime.now(UTC)
    data["exp"] = now + expires_in
    data["iat"] = now
    data["type"] = "action"
    data["purpose"] = purpose
    return jwt.encode(data, settings.jwt_access_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_access_secret, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != "access":
            raise InvalidTokenError()
        return payload
    except JWTError as exc:
        if "expired" in str(exc):
            raise TokenExpiredError()
        raise InvalidTokenError()


def decode_refresh_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_refresh_secret, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != "refresh":
            raise InvalidTokenError()
        return payload
    except JWTError as exc:
        if "expired" in str(exc):
            raise TokenExpiredError()
        raise InvalidTokenError()


def decode_action_token(token: str, *, expected_purpose: str | None = None) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_access_secret, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != "action":
            raise InvalidTokenError()
        if expected_purpose and payload.get("purpose") != expected_purpose:
            raise InvalidTokenError()
        return payload
    except JWTError as exc:
        if "expired" in str(exc):
            raise TokenExpiredError()
        raise InvalidTokenError()
