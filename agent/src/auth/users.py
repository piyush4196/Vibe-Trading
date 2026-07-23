"""Local multi-user authentication for the Vibe-Trading API.

Users live in ``~/.vibe-trading/users.json`` with scrypt password hashes.
Login issues HS256 JWTs (PyJWT). The shared ``API_AUTH_KEY`` remains valid as
an admin/machine credential for backward compatibility.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from src.config.paths import get_runtime_root

Role = Literal["admin", "user"]

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,64}$")
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 64
_lock = threading.Lock()


class AuthError(RuntimeError):
    """Raised for user/auth failures that should surface to the CLI/API."""


@dataclass
class UserRecord:
    username: str
    password_hash: str
    role: Role = "user"
    created_at: str = ""
    disabled: bool = False
    display_name: str = ""

    def public_dict(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "role": self.role,
            "created_at": self.created_at,
            "disabled": self.disabled,
            "display_name": self.display_name or self.username,
        }


def users_path() -> Path:
    return get_runtime_root() / "users.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """Return ``scrypt$<salt_hex>$<hash_hex>``."""
    if not isinstance(password, str) or not password:
        raise AuthError("password must be a non-empty string")
    if len(password) < 8:
        raise AuthError("password must be at least 8 characters")
    salt_b = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt_b,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return f"scrypt${salt_b.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, salt_hex, hash_hex = encoded.split("$", 2)
    except ValueError:
        return False
    if algo != "scrypt":
        return False
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return hmac.compare_digest(digest, expected)


def _load_raw() -> list[dict[str, Any]]:
    path = users_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthError(f"invalid users file at {path}: {exc}") from exc
    if isinstance(data, dict) and "users" in data:
        data = data["users"]
    if not isinstance(data, list):
        raise AuthError(f"invalid users file at {path}: expected a list")
    return data


def _save_raw(rows: list[dict[str, Any]]) -> Path:
    path = users_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "users": rows}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def list_users() -> list[UserRecord]:
    with _lock:
        return [UserRecord(**{k: r.get(k) for k in UserRecord.__dataclass_fields__}) for r in _load_raw()]  # type: ignore[misc]


def get_user(username: str) -> UserRecord | None:
    needle = str(username or "").strip().lower()
    for user in list_users():
        if user.username.lower() == needle:
            return user
    return None


def users_configured() -> bool:
    return any(not u.disabled for u in list_users())


def validate_username(username: str) -> str:
    clean = str(username or "").strip()
    if not _USERNAME_RE.match(clean):
        raise AuthError(
            "username must be 3-64 chars of letters, digits, underscore, dot, or hyphen"
        )
    return clean


def create_user(
    username: str,
    password: str,
    *,
    role: Role = "user",
    display_name: str = "",
    force: bool = False,
) -> UserRecord:
    """Create a local user. Raises ``AuthError`` on validation / conflicts."""
    clean = validate_username(username)
    if role not in ("admin", "user"):
        raise AuthError("role must be 'admin' or 'user'")
    record = UserRecord(
        username=clean,
        password_hash=hash_password(password),
        role=role,  # type: ignore[arg-type]
        created_at=_utc_now_iso(),
        disabled=False,
        display_name=(display_name or clean).strip(),
    )
    with _lock:
        rows = _load_raw()
        for row in rows:
            if str(row.get("username", "")).lower() == clean.lower():
                if not force:
                    raise AuthError(f"user already exists: {clean}")
                row.update(asdict(record))
                _save_raw(rows)
                return record
        rows.append(asdict(record))
        _save_raw(rows)
    return record


def set_password(username: str, password: str) -> UserRecord:
    user = get_user(username)
    if user is None:
        raise AuthError(f"user not found: {username}")
    with _lock:
        rows = _load_raw()
        for row in rows:
            if str(row.get("username", "")).lower() == user.username.lower():
                row["password_hash"] = hash_password(password)
                _save_raw(rows)
                return UserRecord(**{k: row.get(k) for k in UserRecord.__dataclass_fields__})  # type: ignore[misc]
    raise AuthError(f"user not found: {username}")


def set_disabled(username: str, disabled: bool) -> UserRecord:
    user = get_user(username)
    if user is None:
        raise AuthError(f"user not found: {username}")
    with _lock:
        rows = _load_raw()
        for row in rows:
            if str(row.get("username", "")).lower() == user.username.lower():
                row["disabled"] = bool(disabled)
                _save_raw(rows)
                return UserRecord(**{k: row.get(k) for k in UserRecord.__dataclass_fields__})  # type: ignore[misc]
    raise AuthError(f"user not found: {username}")


def authenticate_user(username: str, password: str) -> UserRecord:
    user = get_user(username)
    if user is None or user.disabled:
        raise AuthError("invalid username or password")
    if not verify_password(password, user.password_hash):
        raise AuthError("invalid username or password")
    return user
