"""Read-only + paper-order Upstox connector via the Developer REST API.

Uses ``httpx`` (already a core dependency) — no optional Upstox SDK required.
Covers NSE/BSE equities, NSE indices (Nifty 50 / Bank Nifty / FinNifty /
midcap), stock & index futures/options (NSE_FO / BSE_FO), MCX commodities
(Gold / Silver / Crude Oil), and currency futures (USDINR, EURINR, …).

Paper-vs-live: Upstox has no sandbox environment. Paper mode uses the same API
for market-data reads but simulates orders locally. Live order placement is
structurally refused (Longbridge / Dhan / Shoonya precedent).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from src.config.paths import get_runtime_root
from src.trading.connectors.upstox.instruments import (
    UpstoxInstrumentError,
    resolve_instrument,
)

CONFIG_FILENAME = "upstox.json"

PROFILE_ENVIRONMENTS = {
    "paper": "paper",
    "live-readonly": "live",
    "live": "live",
}

UPSTOX_API_BASE = "https://api.upstox.com"
UPSTOX_API_V2 = f"{UPSTOX_API_BASE}/v2"
UPSTOX_API_V3 = f"{UPSTOX_API_BASE}/v3"

_PAPER_ONLY_ERROR = (
    "Upstox connector is paper-only: it exposes no runtime paper/live "
    "discriminator, so live order placement is not supported. Use an "
    "upstox-paper-* profile."
)

# Project / connector period token → (unit, interval) for Historical Candle V3.
_PERIOD_TO_V3: dict[str, tuple[str, str]] = {
    "1m": ("minutes", "1"),
    "2m": ("minutes", "2"),
    "3m": ("minutes", "3"),
    "5m": ("minutes", "5"),
    "10m": ("minutes", "10"),
    "15m": ("minutes", "15"),
    "30m": ("minutes", "30"),
    "1h": ("hours", "1"),
    "2h": ("hours", "2"),
    "3h": ("hours", "3"),
    "4h": ("hours", "4"),
    "1d": ("days", "1"),
    "1w": ("weeks", "1"),
    "1M": ("months", "1"),
}


class UpstoxDependencyError(RuntimeError):
    """Raised when ``httpx`` is unavailable."""


class UpstoxConfigError(RuntimeError):
    """Raised when the connector configuration is missing or invalid."""


class UpstoxAPIError(RuntimeError):
    """Raised on non-success Upstox HTTP responses."""


@dataclass(frozen=True)
class UpstoxConfig:
    """Upstox connector connection settings.

    Args:
        access_token: Bearer token from the Upstox developer console / OAuth.
        api_key: Optional OAuth client id (app key) for authorize helpers.
        api_secret: Optional OAuth client secret.
        redirect_uri: OAuth redirect URI registered with the Upstox app.
        profile: ``paper``, ``live-readonly`` or ``live``.
        timeout: Network timeout in seconds.
        readonly: Whether order placement is disabled at the profile layer.
    """

    access_token: str = ""
    api_key: str = ""
    api_secret: str = ""
    redirect_uri: str = "http://127.0.0.1"
    profile: str = "paper"
    timeout: float = 30.0
    readonly: bool = True

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None = None) -> "UpstoxConfig":
        payload = dict(data or {})
        profile = str(payload.get("profile") or "paper").strip().lower()
        if profile not in PROFILE_ENVIRONMENTS:
            raise UpstoxConfigError("profile must be 'paper', 'live-readonly' or 'live'")
        return cls(
            access_token=str(payload.get("access_token") or "").strip(),
            api_key=str(payload.get("api_key") or payload.get("client_id") or "").strip(),
            api_secret=str(payload.get("api_secret") or payload.get("client_secret") or "").strip(),
            redirect_uri=str(payload.get("redirect_uri") or "http://127.0.0.1").strip(),
            profile=profile,
            timeout=float(payload.get("timeout") or 30.0),
            readonly=bool(payload.get("readonly", True)),
        )

    def with_overrides(
        self,
        *,
        access_token: str | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        redirect_uri: str | None = None,
        profile: str | None = None,
    ) -> "UpstoxConfig":
        payload = asdict(self)
        if access_token is not None:
            payload["access_token"] = access_token
        if api_key is not None:
            payload["api_key"] = api_key
        if api_secret is not None:
            payload["api_secret"] = api_secret
        if redirect_uri is not None:
            payload["redirect_uri"] = redirect_uri
        if profile is not None:
            payload["profile"] = profile
        return UpstoxConfig.from_mapping(payload)

    @property
    def environment(self) -> str:
        return PROFILE_ENVIRONMENTS.get(self.profile, "paper")

    @property
    def is_paper(self) -> bool:
        return self.environment == "paper"


_OVERRIDE_KEYS = ("access_token", "api_key", "api_secret", "redirect_uri", "profile")


def build_config(
    profile_config: Mapping[str, Any] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> UpstoxConfig:
    """Resolve config: saved file ← profile defaults ← CLI overrides."""
    base = asdict(load_config())
    for key, value in dict(profile_config or {}).items():
        if value is not None:
            base[key] = value
    cfg = UpstoxConfig.from_mapping(base)
    clean = {
        k: v
        for k, v in dict(overrides or {}).items()
        if k in _OVERRIDE_KEYS and v not in (None, "")
    }
    return cfg.with_overrides(**clean) if clean else cfg


def config_path() -> Path:
    return get_runtime_root() / CONFIG_FILENAME


def load_config() -> UpstoxConfig:
    path = config_path()
    if not path.exists():
        return UpstoxConfig()
    try:
        return UpstoxConfig.from_mapping(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise UpstoxConfigError(f"invalid Upstox config at {path}: {exc}") from exc


def save_config(config: UpstoxConfig) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(config), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def upstox_available() -> bool:
    """True when the HTTP client dependency is importable."""
    try:
        import httpx  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def upstox_configured(config: UpstoxConfig | None = None) -> bool:
    """True when an access token is present."""
    cfg = config or load_config()
    return bool(cfg.access_token)


# ---------------------------------------------------------------------------
# Status / account / portfolio
# ---------------------------------------------------------------------------


def check_status(config: UpstoxConfig | None = None) -> dict[str, Any]:
    """Check HTTP client readiness and config completeness.

    Emits Runtime /live/status fields (``connection_state``, ``configured``,
    ``credential_source``, ``last_checked_at``) so the Web UI can show
    connected / not-configured instead of a blank "Status unavailable".
    """
    cfg = config or load_config()
    installed = upstox_available()
    configured = not _missing_fields(cfg)
    credential_source = "runtime_file" if config_path().exists() else None
    paper_guard = "simulated_locally" if cfg.is_paper else "config_declared"
    report: dict[str, Any] = {
        "status": "ok",
        "configured": configured,
        "credential_source": credential_source,
        "connection_state": "connected",
        "error_code": None,
        "error": None,
        "config": _public_config(cfg),
        "sdk": {"package": "httpx", "installed": installed},
        "paper_guard": paper_guard,
        "host": UPSTOX_API_BASE,
        "markets": [
            "NSE_EQ",
            "BSE_EQ",
            "NSE_INDEX",
            "NSE_FO",
            "BSE_FO",
            "MCX_FO",
            "NCD_FO",
        ],
    }

    missing = _missing_fields(cfg)
    if missing:
        report["status"] = "error"
        report["configured"] = False
        report["connection_state"] = "not_configured"
        report["error_code"] = "credentials_missing"
        report["error"] = f"Upstox connector not configured: missing {', '.join(missing)}."
        return report

    if not installed:
        report["status"] = "error"
        report["connection_state"] = "error"
        report["error_code"] = "sdk_missing"
        report["error"] = "Optional dependency missing: install with `pip install httpx`."
        return report

    try:
        get_account_snapshot(cfg)
    except Exception as exc:  # noqa: BLE001 — surface as status error
        code = _connection_error_code(exc)
        report["status"] = "error"
        report["connection_state"] = "error"
        report["error_code"] = code
        report["error"] = str(exc)
        return report

    report["account"] = {
        "profile": cfg.profile,
        "is_paper": cfg.is_paper,
    }
    report["last_checked_at"] = datetime.now(timezone.utc).isoformat()
    return report


def _connection_error_code(exc: Exception) -> str:
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return "network_unreachable"
    text = type(exc).__name__.lower() + " " + str(exc).lower()
    if any(token in text for token in ("auth", "token", "permission", "unauthorized", "401", "403")):
        return "authentication_failed"
    return "broker_error"


def get_account_snapshot(config: UpstoxConfig | None = None) -> dict[str, Any]:
    """Fetch profile + fund/margin for the configured account.

    When ``is_paper``, also attaches the local paper wallet so cash/equity/P&L
    reflect deposited play money rather than only the live Upstox margin API.
    """
    cfg = config or load_config()
    if cfg.is_paper:
        from src.trading.paper_wallet import snapshot as paper_snapshot

        paper = paper_snapshot()
        return {
            "status": "ok",
            "profile": cfg.profile,
            "is_paper": True,
            "host": UPSTOX_API_BASE,
            "account": {
                "currency": paper.get("currency") or "INR",
                "cash": paper.get("cash"),
                "equity": paper.get("equity"),
                "buying_power": paper.get("buying_power"),
                "realized_pnl": paper.get("realized_pnl"),
                "unrealized_pnl": paper.get("unrealized_pnl"),
                "total_pnl": paper.get("total_pnl"),
                "total_deposited": paper.get("total_deposited"),
                "source": "local_paper_wallet",
            },
            "paper_wallet": paper,
        }

    profile = _api_get(cfg, f"{UPSTOX_API_V2}/user/profile")
    funds = _api_get(cfg, f"{UPSTOX_API_V2}/user/get-funds-and-margin")
    equity = (funds.get("data") or {}).get("equity") or {}
    commodity = (funds.get("data") or {}).get("commodity") or {}
    user = profile.get("data") or {}
    return {
        "status": "ok",
        "profile": cfg.profile,
        "is_paper": cfg.is_paper,
        "host": UPSTOX_API_BASE,
        "account": {
            "currency": "INR",
            "user_id": user.get("user_id"),
            "user_name": user.get("user_name"),
            "exchanges": user.get("exchanges") or [],
            "products": user.get("products") or [],
            "available_margin": equity.get("available_margin"),
            "used_margin": equity.get("used_margin"),
            "commodity_available_margin": commodity.get("available_margin"),
        },
    }


def get_positions(config: UpstoxConfig | None = None) -> dict[str, Any]:
    """Fetch short-term positions (paper wallet when in paper mode)."""
    cfg = config or load_config()
    if cfg.is_paper:
        from src.trading.paper_wallet import snapshot as paper_snapshot

        paper = paper_snapshot()
        rows = []
        for item in paper.get("positions") or []:
            rows.append(
                {
                    "symbol": item.get("symbol") or "",
                    "instrument_key": item.get("instrument_key") or "",
                    "exchange": "",
                    "product_type": "paper",
                    "quantity": item.get("quantity") or 0,
                    "average_cost": item.get("avg_price") or 0,
                    "current_price": item.get("last_price") or 0,
                    "unrealized_pnl": item.get("unrealized_pnl") or 0,
                    "realized_pnl": 0,
                }
            )
        return {
            "status": "ok",
            "profile": cfg.profile,
            "is_paper": True,
            "positions": rows,
            "source": "local_paper_wallet",
        }

    payload = _api_get(cfg, f"{UPSTOX_API_V2}/portfolio/short-term-positions")
    rows = []
    for item in _as_list(payload.get("data")):
        rows.append(
            {
                "symbol": item.get("trading_symbol") or item.get("tradingsymbol") or "",
                "instrument_key": item.get("instrument_token") or "",
                "exchange": item.get("exchange", ""),
                "product_type": item.get("product", ""),
                "quantity": item.get("quantity", 0),
                "average_cost": item.get("average_price") or item.get("buy_price") or 0,
                "current_price": item.get("last_price", 0),
                "unrealized_pnl": item.get("unrealised") or item.get("pnl") or 0,
                "realized_pnl": item.get("realised", 0),
            }
        )
    return {
        "status": "ok",
        "profile": cfg.profile,
        "is_paper": cfg.is_paper,
        "positions": rows,
    }


def get_open_orders(
    config: UpstoxConfig | None = None,
    *,
    include_executions: bool = False,
) -> dict[str, Any]:
    """Fetch open orders (and optionally recent executions)."""
    cfg = config or load_config()
    payload = _api_get(cfg, f"{UPSTOX_API_V2}/order/retrieve-all")
    open_orders: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []
    for item in _as_list(payload.get("data")):
        order_dict = _order_to_dict(item)
        status = str(item.get("status") or item.get("order_status") or "").upper()
        if status in ("OPEN", "PENDING", "TRIGGER_PENDING", "AFTER_MARKET_ORDER_REQ"):
            open_orders.append(order_dict)
        elif include_executions and status in ("COMPLETE", "COMPLETED", "TRADED", "FILLED"):
            executions.append(order_dict)
    result: dict[str, Any] = {
        "status": "ok",
        "profile": cfg.profile,
        "is_paper": cfg.is_paper,
        "open_orders": open_orders,
    }
    if include_executions:
        result["executions"] = executions
    return result


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------


def get_quote(
    symbol: str,
    *,
    config: UpstoxConfig | None = None,
    instrument_key: str | None = None,
    segment: str | None = None,
    exchange: str | None = None,
) -> dict[str, Any]:
    """Fetch LTP + OHLC for a symbol / instrument_key."""
    cfg = config or load_config()
    try:
        resolved = _resolve(symbol, instrument_key=instrument_key, segment=segment, exchange=exchange)
    except UpstoxInstrumentError as exc:
        return {"status": "error", "error": str(exc), "symbol": symbol}

    key = resolved["instrument_key"]
    try:
        ohlc = _api_get(
            cfg,
            f"{UPSTOX_API_V2}/market-quote/ohlc",
            params={"instrument_key": key, "interval": "1d"},
        )
        ltp = _api_get(
            cfg,
            f"{UPSTOX_API_V2}/market-quote/ltp",
            params={"instrument_key": key},
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc), "symbol": symbol, "instrument_key": key}

    ohlc_row = _first_quote_row(ohlc.get("data"))
    ltp_row = _first_quote_row(ltp.get("data"))
    return {
        "status": "ok",
        "symbol": symbol,
        "instrument_key": key,
        "trading_symbol": resolved.get("trading_symbol"),
        "segment": resolved.get("segment"),
        "quote": {
            "ltp": (ltp_row or {}).get("last_price") or (ohlc_row or {}).get("last_price"),
            "open": _nested(ohlc_row, "ohlc", "open"),
            "high": _nested(ohlc_row, "ohlc", "high"),
            "low": _nested(ohlc_row, "ohlc", "low"),
            "close": _nested(ohlc_row, "ohlc", "close"),
            "volume": (ohlc_row or {}).get("volume"),
        },
    }


def get_historical_bars(
    symbol: str,
    *,
    config: UpstoxConfig | None = None,
    instrument_key: str | None = None,
    segment: str | None = None,
    exchange: str | None = None,
    period: str = "1d",
    limit: int = 90,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Fetch historical OHLCV bars via Historical Candle Data V3.

    ``period`` tokens: ``1m``, ``5m``, ``15m``, ``30m``, ``1h``, ``4h``, ``1d``,
    ``1w``, ``1M``. Also accepts ``exchange`` (NSE/BSE/MCX/CDS/NFO) for the
    india_broker loader bridge.
    """
    cfg = config or load_config()
    try:
        resolved = _resolve(
            symbol,
            instrument_key=instrument_key,
            segment=segment,
            exchange=exchange,
        )
    except UpstoxInstrumentError as exc:
        return {"status": "error", "error": str(exc), "symbol": symbol}

    unit_interval = _PERIOD_TO_V3.get(str(period).strip())
    if unit_interval is None:
        # Accept runner-style tokens.
        normalized = str(period).strip().upper()
        alias = {
            "1D": "1d",
            "1H": "1h",
            "4H": "4h",
            "1W": "1w",
        }.get(normalized)
        unit_interval = _PERIOD_TO_V3.get(alias or "")
    if unit_interval is None:
        return {
            "status": "error",
            "error": f"unsupported period '{period}'; expected one of {sorted(_PERIOD_TO_V3)}",
            "symbol": symbol,
        }
    unit, interval = unit_interval

    to_dt = (
        datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if end_date
        else datetime.now(timezone.utc)
    )
    if start_date:
        from_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        # Heuristic window from limit + period.
        if unit == "minutes":
            from_dt = to_dt - timedelta(days=min(max(limit // 50, 1), 30))
        elif unit == "hours":
            from_dt = to_dt - timedelta(days=min(max(limit // 6, 1), 90))
        elif unit == "weeks":
            from_dt = to_dt - timedelta(weeks=min(limit, 520))
        elif unit == "months":
            from_dt = to_dt - timedelta(days=min(limit * 31, 3650))
        else:
            from_dt = to_dt - timedelta(days=min(max(limit * 2, 30), 3650))

    key = resolved["instrument_key"]
    encoded_key = quote(key, safe="")
    to_s = to_dt.strftime("%Y-%m-%d")
    from_s = from_dt.strftime("%Y-%m-%d")
    url = f"{UPSTOX_API_V3}/historical-candle/{encoded_key}/{unit}/{interval}/{to_s}/{from_s}"

    try:
        payload = _api_get(cfg, url)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc), "symbol": symbol, "instrument_key": key}

    bars = []
    for candle in _as_list((payload.get("data") or {}).get("candles")):
        if not isinstance(candle, (list, tuple)) or len(candle) < 6:
            continue
        bars.append(
            {
                "time": candle[0],
                "open": candle[1],
                "high": candle[2],
                "low": candle[3],
                "close": candle[4],
                "volume": candle[5],
                "oi": candle[6] if len(candle) > 6 else 0,
            }
        )

    return {
        "status": "ok",
        "symbol": symbol,
        "instrument_key": key,
        "trading_symbol": resolved.get("trading_symbol"),
        "segment": resolved.get("segment"),
        "period": period,
        "bars": bars[-limit:] if limit else bars,
    }


# ---------------------------------------------------------------------------
# Orders (paper-only)
# ---------------------------------------------------------------------------


def place_order(
    config: UpstoxConfig | None = None,
    *,
    symbol: str,
    side: str,
    quantity: float | None = None,
    notional: float | None = None,
    order_type: str = "market",
    limit_price: float | None = None,
    time_in_force: str = "day",
    instrument_key: str | None = None,
    segment: str | None = None,
    exchange: str | None = None,
    product: str = "D",
) -> dict[str, Any]:
    """Place a PAPER-ONLY order (simulated locally).

    Upstox exposes no sandbox / runtime paper discriminator, so live order
    placement is refused. ``product`` mirrors Upstox codes: ``D`` delivery,
    ``I`` intraday, ``MTF``.
    """
    del notional, time_in_force  # reserved for a future live path
    cfg = config or load_config()

    if not cfg.is_paper:
        return {"status": "error", "error": _PAPER_ONLY_ERROR}

    clean_symbol = str(symbol or "").strip().upper()
    if not clean_symbol:
        return {"status": "error", "error": "symbol is required"}

    side_token = str(side or "").strip().upper()
    if side_token not in ("BUY", "SELL"):
        return {"status": "error", "error": "side must be 'buy' or 'sell'"}

    type_token = str(order_type or "").strip().upper()
    if type_token not in ("MARKET", "LIMIT"):
        return {"status": "error", "error": "order_type must be 'market' or 'limit'"}

    if quantity is None or float(quantity) <= 0:
        return {"status": "error", "error": "quantity must be positive"}

    qty = int(float(quantity))
    if type_token == "LIMIT" and limit_price is None:
        return {"status": "error", "error": "limit order requires limit_price"}

    try:
        resolved = _resolve(
            symbol,
            instrument_key=instrument_key,
            segment=segment,
            exchange=exchange,
        )
    except UpstoxInstrumentError:
        # Paper simulation must work offline / without the instrument master —
        # fall back to the raw symbol the way Dhan/Shoonya do.
        resolved = {
            "instrument_key": instrument_key or clean_symbol,
            "trading_symbol": clean_symbol,
            "segment": segment,
        }

    price = float(limit_price) if limit_price is not None else 0.0
    key = resolved["instrument_key"]

    # Resolve a mark/fill price so the paper wallet can debit cash & track P&L.
    fill_price = price
    if type_token == "MARKET" or fill_price <= 0:
        try:
            quote = get_quote(
                clean_symbol,
                config=cfg,
                instrument_key=key,
                segment=segment,
                exchange=exchange,
            )
            ltp = float((quote.get("quote") or {}).get("ltp") or 0)
            if ltp > 0:
                fill_price = ltp
        except Exception:
            fill_price = fill_price or 0.0

    order_id = f"PAPER-{key}-{side_token}-{qty}"
    result: dict[str, Any] = {
        "status": "ok",
        "order_id": order_id,
        "symbol": clean_symbol,
        "instrument_key": key,
        "side": side_token.lower(),
        "profile": cfg.profile,
        "is_paper": True,
        "paper_guard": "simulated_locally",
        "order_type": type_token.lower(),
        "quantity": qty,
        "limit_price": price if type_token == "LIMIT" else None,
        "fill_price": fill_price if fill_price > 0 else None,
        "order_status": "simulated_fill",
        "segment": resolved.get("segment"),
        "product": product,
    }

    if fill_price > 0:
        try:
            from src.trading.paper_wallet import PaperWalletError, apply_fill

            wallet = apply_fill(
                symbol=clean_symbol,
                side=side_token.lower(),
                quantity=float(qty),
                price=float(fill_price),
                instrument_key=str(key),
                order_id=order_id,
                broker="upstox-paper",
            )
            result["paper_wallet"] = {
                "cash": wallet.get("cash"),
                "equity": wallet.get("equity"),
                "total_pnl": wallet.get("total_pnl"),
                "currency": wallet.get("currency"),
            }
            result["fill"] = wallet.get("fill")
        except PaperWalletError as exc:
            return {
                "status": "error",
                "error": str(exc),
                "hint": "Deposit paper money first: Settings → Paper wallet, or POST /paper/deposit",
            }
    else:
        result["warning"] = (
            "Simulated fill recorded without wallet update (no fill price). "
            "Deposit paper cash in Settings and ensure market quotes are available."
        )

    return result


def cancel_order(
    config: UpstoxConfig | None = None,
    order_id: str = "",
    *,
    symbol: str | None = None,
) -> dict[str, Any]:
    """Cancel a PAPER-ONLY order (simulated locally)."""
    cfg = config or load_config()
    if not cfg.is_paper:
        return {"status": "error", "error": _PAPER_ONLY_ERROR}

    clean_id = str(order_id or "").strip()
    if not clean_id:
        return {"status": "error", "error": "order_id is required"}

    return {
        "status": "ok",
        "order_id": clean_id,
        "symbol": symbol.strip().upper() if isinstance(symbol, str) and symbol.strip() else None,
        "profile": cfg.profile,
        "is_paper": True,
        "cancelled": True,
    }


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


def _require_httpx():
    try:
        import httpx
    except ModuleNotFoundError as exc:
        raise UpstoxDependencyError(
            "httpx is not installed; run `pip install httpx`."
        ) from exc
    return httpx


def _missing_fields(cfg: UpstoxConfig) -> list[str]:
    missing = []
    if not cfg.access_token:
        missing.append("access_token")
    return missing


def _public_config(cfg: UpstoxConfig) -> dict[str, Any]:
    data = asdict(cfg)
    if data.get("access_token"):
        data["access_token"] = data["access_token"][:8] + "***"
    if data.get("api_secret"):
        data["api_secret"] = "***redacted***"
    return data


def _headers(cfg: UpstoxConfig) -> dict[str, str]:
    if not cfg.access_token:
        raise UpstoxConfigError(
            "Upstox connector not configured: set access_token in "
            "~/.vibe-trading/upstox.json"
        )
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {cfg.access_token}",
    }


def _api_get(
    cfg: UpstoxConfig,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    httpx = _require_httpx()
    with httpx.Client(timeout=cfg.timeout, follow_redirects=True) as client:
        response = client.get(url, headers=_headers(cfg), params=dict(params or {}))
    try:
        payload = response.json()
    except ValueError as exc:
        raise UpstoxAPIError(
            f"Upstox non-JSON response ({response.status_code}) for {url}"
        ) from exc
    if response.status_code >= 400:
        message = payload.get("message") if isinstance(payload, dict) else None
        errors = payload.get("errors") if isinstance(payload, dict) else None
        detail = message or errors or payload
        raise UpstoxAPIError(f"Upstox HTTP {response.status_code}: {detail}")
    if isinstance(payload, dict) and str(payload.get("status", "success")).lower() not in (
        "success",
        "ok",
        "",
    ):
        raise UpstoxAPIError(f"Upstox API error: {payload}")
    return payload if isinstance(payload, dict) else {"data": payload}


def _resolve(
    symbol: str,
    *,
    instrument_key: str | None = None,
    segment: str | None = None,
    exchange: str | None = None,
) -> dict[str, Any]:
    if instrument_key:
        return {
            "instrument_key": str(instrument_key).strip(),
            "trading_symbol": symbol,
            "segment": str(instrument_key).split("|", 1)[0],
        }
    return resolve_instrument(symbol, segment=segment, exchange=exchange)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _first_quote_row(data: Any) -> dict[str, Any] | None:
    if isinstance(data, Mapping):
        for value in data.values():
            if isinstance(value, Mapping):
                return dict(value)
        return dict(data)
    return None


def _nested(row: Mapping[str, Any] | None, *keys: str) -> Any:
    data: Any = row
    for key in keys:
        if not isinstance(data, Mapping):
            return None
        data = data.get(key)
    return data


def _order_to_dict(item: Any) -> dict[str, Any]:
    return {
        "order_id": str(item.get("order_id") or item.get("orderId") or ""),
        "symbol": item.get("trading_symbol") or item.get("tradingsymbol") or "",
        "instrument_key": item.get("instrument_token") or item.get("instrument_key") or "",
        "side": str(item.get("transaction_type") or item.get("transactionType") or "").lower(),
        "order_type": str(item.get("order_type") or item.get("orderType") or "").lower(),
        "quantity": item.get("quantity", 0),
        "filled_qty": item.get("filled_quantity") or item.get("filledQty") or 0,
        "price": item.get("price", 0),
        "status": item.get("status") or item.get("order_status") or "",
        "product": item.get("product", ""),
        "exchange": item.get("exchange", ""),
    }
