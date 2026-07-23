"""Auth routes — login, whoami, SSE tickets.

Mounted by ``agent/api_server.py`` via ``register_auth_routes(app, ...)``.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field

AuthDep = Callable[..., Awaitable[Any] | Any]


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=256)


def register_auth_routes(
    app: FastAPI,
    require_auth: AuthDep | None = None,
) -> None:
    """Mount the auth helper routes onto ``app``."""
    if require_auth is None:
        import sys as _sys

        host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
        if host is None:  # pragma: no cover
            raise RuntimeError(
                "register_auth_routes: api_server module not in sys.modules; "
                "pass require_auth explicitly"
            )
        require_auth = host.require_auth

    from src.api.security import _mint_sse_ticket, _reject_cross_site_browser_request
    from src.auth.tokens import mint_access_token
    from src.auth.users import AuthError, authenticate_user, list_users, users_configured

    @app.post("/auth/login")
    async def login(body: LoginRequest, request: Request) -> dict[str, Any]:
        """Exchange username/password for a Bearer JWT access token."""
        _reject_cross_site_browser_request(request)
        try:
            user = authenticate_user(body.username, body.password)
        except AuthError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
        return mint_access_token(user)

    @app.get("/auth/me", dependencies=[Depends(require_auth)])
    async def me(request: Request) -> dict[str, Any]:
        """Return the authenticated principal (API key or user JWT)."""
        user = getattr(request.state, "user", None) or {"username": "unknown", "role": "unknown"}
        return {
            "auth_kind": getattr(request.state, "auth_kind", "unknown"),
            "user": user,
            "users_configured": users_configured(),
        }

    @app.get("/auth/status")
    async def auth_status() -> dict[str, Any]:
        """Public auth capability probe (no secrets)."""
        return {
            "users_configured": users_configured(),
            "user_count": len(list_users()),
            "login_path": "/auth/login",
            "supports_api_key": True,
            "supports_user_jwt": True,
        }

    @app.post("/auth/sse-ticket", dependencies=[Depends(require_auth)])
    async def mint_sse_ticket() -> dict[str, str]:
        """Mint a single-use, ~60s ticket for a browser EventSource connection."""
        return {"ticket": _mint_sse_ticket()}
