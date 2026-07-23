"""JWT access tokens for local user sessions."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt

from src.auth.users import UserRecord
from src.config.accessor import get_env_config
from src.config.paths import get_runtime_root

ALGORITHM = "HS256"
DEFAULT_TTL_SECONDS = 60 * 60 * 12  # 12h


class TokenError(RuntimeError):
    """Invalid or expired access token."""


def _secret_path() -> Path:
    return get_runtime_root() / "auth_secret"


def get_jwt_secret() -> str:
    """Resolve signing secret: AUTH_JWT_SECRET → API_AUTH_KEY → persisted file."""
    cfg = get_env_config().api
    explicit = (getattr(cfg, "auth_jwt_secret", "") or "").strip()
    if explicit:
        return explicit
    if cfg.api_auth_key:
        return cfg.api_auth_key
    path = _secret_path()
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_urlsafe(48)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return value


def get_token_ttl_seconds() -> int:
    cfg = get_env_config().api
    raw = getattr(cfg, "auth_jwt_ttl_seconds", 0) or 0
    try:
        ttl = int(raw)
    except (TypeError, ValueError):
        ttl = 0
    return ttl if ttl > 0 else DEFAULT_TTL_SECONDS


def mint_access_token(user: UserRecord, *, ttl_seconds: int | None = None) -> dict[str, Any]:
    ttl = int(ttl_seconds if ttl_seconds is not None else get_token_ttl_seconds())
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.username,
        "role": user.role,
        "typ": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
    }
    token = jwt.encode(payload, get_jwt_secret(), algorithm=ALGORITHM)
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": ttl,
        "user": user.public_dict(),
    }


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise TokenError("invalid or expired token") from exc
    if str(payload.get("typ") or "") != "access":
        raise TokenError("invalid token type")
    if not payload.get("sub"):
        raise TokenError("invalid token subject")
    return payload
