"""Tests for GET /dashboard/summary."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.unit


@pytest.fixture()
def dash_home(tmp_path, monkeypatch):
    from src.api import dashboard_routes as dash
    from src.config.accessor import reset_env_config

    monkeypatch.setattr(dash, "get_runtime_root", lambda: tmp_path)
    monkeypatch.setenv("API_AUTH_KEY", "")
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    reset_env_config()
    return tmp_path


def _seed_outcomes(root: Path) -> None:
    db = root / "watcher" / "watcher.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE outcomes (
            signal_id TEXT PRIMARY KEY,
            created_at TEXT,
            closed_at TEXT,
            pnl_pct REAL,
            holding_seconds REAL,
            exit_reason TEXT,
            payload TEXT
        );
        CREATE TABLE positions (
            signal_id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            status TEXT
        );
        """
    )
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO outcomes VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "sig1",
            now,
            now,
            1.5,
            60,
            "target",
            json.dumps({"instrument": "NIFTY", "side": "BUY"}),
        ),
    )
    conn.execute(
        "INSERT INTO positions VALUES (?, ?, ?)",
        ("sig2", "{}", "open"),
    )
    conn.commit()
    conn.close()

    audit = root / "live" / "audit.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(
        json.dumps(
            {
                "audit_id": "la_test",
                "ts": now,
                "kind": "order_placed",
                "outcome": "accepted",
                "server": "upstox",
                "intent_normalized": "buy 1 NIFTY paper",
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_dashboard_summary_with_watcher_data(dash_home):
    _seed_outcomes(dash_home)
    from src.api.dashboard_routes import build_dashboard_summary

    summary = build_dashboard_summary()
    assert summary.today.trades >= 1
    assert summary.today.pnl_pct == pytest.approx(1.5)
    assert summary.open_positions == 1
    assert summary.recent_trades[0].instrument == "NIFTY"
    assert summary.recent_audit[0].kind == "order_placed"
    assert summary.orders.can_place_orders is True
    assert len(summary.daily) == 30
    assert len(summary.monthly) == 12


def test_dashboard_route_local(dash_home, monkeypatch):
    import api_server

    monkeypatch.setattr(api_server, "_API_KEY", "")
    _seed_outcomes(dash_home)
    client = TestClient(api_server.app, client=("127.0.0.1", 50000))
    response = client.get("/dashboard/summary")
    assert response.status_code == 200
    body = response.json()
    assert "today" in body
    assert "daily" in body
    assert body["orders"]["paper_supported"] is True
