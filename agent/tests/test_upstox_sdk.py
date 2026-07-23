"""Upstox SDK unit tests — HTTP mocked, no live credentials required."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.trading.connectors.upstox import sdk as up

pytestmark = pytest.mark.unit


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    def __init__(self, routes: dict[str, dict]):
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, headers=None, params=None):
        self.calls.append((url, dict(params or {})))
        for key, payload in self.routes.items():
            if key in url:
                return _FakeResponse(payload)
        return _FakeResponse({"status": "error", "message": f"unmocked {url}"}, status_code=404)


def test_historical_bars_parses_v3_candles(monkeypatch) -> None:
    routes = {
        "historical-candle": {
            "status": "success",
            "data": {
                "candles": [
                    ["2024-01-02T00:00:00+05:30", 100, 110, 95, 105, 1000, 0],
                    ["2024-01-03T00:00:00+05:30", 105, 112, 104, 110, 1200, 0],
                ]
            },
        }
    }
    fake = _FakeClient(routes)
    monkeypatch.setattr(up, "_require_httpx", lambda: SimpleNamespace(Client=lambda **kw: fake))
    monkeypatch.setattr(
        up,
        "_resolve",
        lambda symbol, **kw: {
            "instrument_key": "NSE_EQ|INE002A01018",
            "trading_symbol": "RELIANCE",
            "segment": "NSE_EQ",
        },
    )

    cfg = up.UpstoxConfig(access_token="tok-test", profile="paper")
    out = up.get_historical_bars("RELIANCE.NS", config=cfg, period="1d", limit=10)
    assert out["status"] == "ok"
    assert len(out["bars"]) == 2
    assert out["bars"][0]["open"] == 100
    assert out["instrument_key"] == "NSE_EQ|INE002A01018"
    assert any("historical-candle" in url for url, _ in fake.calls)


def test_get_quote_combines_ltp_and_ohlc(monkeypatch) -> None:
    routes = {
        "market-quote/ohlc": {
            "status": "success",
            "data": {
                "NSE_EQ:RELIANCE": {
                    "last_price": 2500.0,
                    "ohlc": {"open": 2480, "high": 2510, "low": 2470, "close": 2490},
                    "volume": 1_000_000,
                }
            },
        },
        "market-quote/ltp": {
            "status": "success",
            "data": {"NSE_EQ:RELIANCE": {"last_price": 2501.5}},
        },
    }
    fake = _FakeClient(routes)
    monkeypatch.setattr(up, "_require_httpx", lambda: SimpleNamespace(Client=lambda **kw: fake))
    monkeypatch.setattr(
        up,
        "_resolve",
        lambda symbol, **kw: {
            "instrument_key": "NSE_EQ|INE002A01018",
            "trading_symbol": "RELIANCE",
            "segment": "NSE_EQ",
        },
    )
    out = up.get_quote("RELIANCE", config=up.UpstoxConfig(access_token="tok"))
    assert out["status"] == "ok"
    assert out["quote"]["ltp"] == 2501.5
    assert out["quote"]["open"] == 2480


def test_unsupported_period_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        up,
        "_resolve",
        lambda symbol, **kw: {"instrument_key": "NSE_EQ|X", "trading_symbol": "X", "segment": "NSE_EQ"},
    )
    out = up.get_historical_bars(
        "RELIANCE", config=up.UpstoxConfig(access_token="tok"), period="9d"
    )
    assert out["status"] == "error"
    assert "unsupported period" in out["error"]
