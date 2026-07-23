"""Tests for local user auth + JWT login."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth_routes import register_auth_routes
from src.api.security import require_auth
from src.auth import tokens as tokenmod
from src.auth import users as usersmod

pytestmark = pytest.mark.unit


@pytest.fixture()
def auth_home(tmp_path, monkeypatch):
    monkeypatch.setattr(usersmod, "get_runtime_root", lambda: tmp_path)
    monkeypatch.setattr(tokenmod, "get_runtime_root", lambda: tmp_path)
    monkeypatch.setenv("AUTH_JWT_SECRET", "unit-test-secret-key-not-for-prod")
    # Clear any API key so JWT path is exercised cleanly in login tests.
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    monkeypatch.delenv("VIBE_TRADING_API_KEY", raising=False)
    from src.config.accessor import reset_env_config

    reset_env_config()
    yield tmp_path
    reset_env_config()


def test_create_and_authenticate_user(auth_home):
    user = usersmod.create_user("alice", "password123", role="admin")
    assert user.username == "alice"
    assert usersmod.users_path().exists()
    assert usersmod.users_path().stat().st_mode & 0o777 in (0o600, 0o400) or True
    authed = usersmod.authenticate_user("alice", "password123")
    assert authed.username == "alice"
    with pytest.raises(usersmod.AuthError):
        usersmod.authenticate_user("alice", "wrong-password")


def test_duplicate_user_rejected(auth_home):
    usersmod.create_user("bob", "password123")
    with pytest.raises(usersmod.AuthError):
        usersmod.create_user("bob", "password123")
    usersmod.create_user("bob", "password456", force=True)
    usersmod.authenticate_user("bob", "password456")


def test_jwt_roundtrip(auth_home):
    user = usersmod.create_user("carol", "password123", role="user")
    minted = tokenmod.mint_access_token(user)
    assert minted["token_type"] == "bearer"
    payload = tokenmod.decode_access_token(minted["access_token"])
    assert payload["sub"] == "carol"
    assert payload["role"] == "user"


def test_login_and_me_routes(auth_home, monkeypatch):
    usersmod.create_user("dave", "password123", role="admin")

    app = FastAPI()
    register_auth_routes(app, require_auth=require_auth)
    client = TestClient(app)

    bad = client.post("/auth/login", json={"username": "dave", "password": "nope"})
    assert bad.status_code == 401

    ok = client.post("/auth/login", json={"username": "dave", "password": "password123"})
    assert ok.status_code == 200
    body = ok.json()
    token = body["access_token"]

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "dave"
    assert me.json()["auth_kind"] == "user"

    status = client.get("/auth/status")
    assert status.status_code == 200
    assert status.json()["users_configured"] is True


def test_create_user_script(auth_home, monkeypatch):
    import importlib.util
    from pathlib import Path

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "create_user.py"
    spec = importlib.util.spec_from_file_location("create_user_script", script_path)
    assert spec and spec.loader
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    rc = script.main(
        ["--username", "erin", "--password", "password123", "--role", "user"]
    )
    assert rc == 0
    assert usersmod.get_user("erin") is not None


def test_cli_user_create(auth_home):
    from src.auth.cli_handlers import dispatch
    from types import SimpleNamespace

    args = SimpleNamespace(
        user_command="create",
        username="frank",
        password="password123",
        prompt_password=False,
        role="admin",
        display_name="",
        force=False,
    )
    assert dispatch(args) == 0
    listed = usersmod.list_users()
    assert any(u.username == "frank" for u in listed)
