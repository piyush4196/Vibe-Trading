"""Tests for the local paper trading wallet."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


@pytest.fixture()
def paper_home(tmp_path, monkeypatch):
    from src.config.accessor import reset_env_config
    from src.trading import paper_wallet as pw

    monkeypatch.setattr(pw, "get_runtime_root", lambda: tmp_path)
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    reset_env_config()
    return tmp_path


def test_deposit_buy_sell_pnl(paper_home):
    from src.trading.paper_wallet import apply_fill, deposit, snapshot

    deposit(100_000, note="seed", currency="INR")
    snap = snapshot()
    assert snap["cash"] == 100_000
    assert snap["total_deposited"] == 100_000

    buy = apply_fill(symbol="RELIANCE", side="buy", quantity=10, price=1000)
    assert buy["cash"] == 90_000
    assert buy["open_positions"] == 1

    # Mark higher via sell — realize profit
    sell = apply_fill(symbol="RELIANCE", side="sell", quantity=10, price=1100)
    assert sell["cash"] == 101_000
    assert sell["realized_pnl"] == pytest.approx(1000)
    assert sell["open_positions"] == 0
    assert sell["total_pnl"] == pytest.approx(1000)


def test_insufficient_cash(paper_home):
    from src.trading.paper_wallet import PaperWalletError, apply_fill, deposit

    deposit(1000)
    with pytest.raises(PaperWalletError, match="insufficient"):
        apply_fill(symbol="NIFTY", side="buy", quantity=10, price=200)


def test_paper_api_deposit(paper_home, monkeypatch):
    import api_server

    monkeypatch.setattr(api_server, "_API_KEY", "")
    client = TestClient(api_server.app, client=("127.0.0.1", 50000))

    bad = client.post("/paper/deposit", json={"amount": -1})
    assert bad.status_code == 422

    ok = client.post("/paper/deposit", json={"amount": 50000, "note": "test", "currency": "INR"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["cash"] == 50000
    assert body["currency"] == "INR"

    wallet = client.get("/paper/wallet")
    assert wallet.status_code == 200
    assert wallet.json()["cash"] == 50000

    dash = client.get("/dashboard/summary")
    assert dash.status_code == 200
    assert dash.json()["paper_wallet"]["cash"] == 50000
