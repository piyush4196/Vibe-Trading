"""Opt-in confidence-gated order bridge for the market watcher.

When ``WatcherConfig.auto_trade_enabled`` is True and a signal clears the
confidence / RR gates, this module places a paper (or mandate-gated live)
order via the selected trading connector. Exit events from the position
monitor can close that quantity with an opposite-side order.

India brokers (Upstox / Dhan / Shoonya) only support **paper** placement —
live order placement remains refused by those connectors.
"""

from __future__ import annotations

import logging
from typing import Any

from src.watcher.config import WatcherConfig
from src.watcher.models import OpenPosition, Side, Signal

logger = logging.getLogger(__name__)


def _profile_id(config: WatcherConfig) -> str | None:
    pid = (config.auto_trade_profile_id or "").strip()
    return pid or None


def should_auto_trade(signal: Signal, config: WatcherConfig) -> tuple[bool, str]:
    """Return whether a signal may place an order under current config."""
    if not config.auto_trade_enabled:
        return False, "auto_trade_disabled"
    if config.dry_run:
        return False, "dry_run"
    if signal.confidence < config.min_confidence:
        return False, f"confidence {signal.confidence:.1f} < {config.min_confidence}"
    qty = float(config.auto_trade_quantity or 0)
    if qty <= 0:
        return False, "auto_trade_quantity must be positive"
    allow = [s.strip().upper() for s in (config.auto_trade_symbols or []) if s and str(s).strip()]
    if allow:
        inst = signal.instrument.upper()
        base = inst.split("-")[0].split()[0]
        if inst not in allow and base not in allow:
            return False, f"symbol {signal.instrument} not in auto_trade_symbols"
    return True, "ok"


def entry_succeeded(trade: dict[str, Any]) -> bool:
    """True when ``place_entry_order`` actually submitted a non-error order."""
    status = str(trade.get("status") or "").lower()
    if status in ("skipped", "error", "rejected", "unsupported", ""):
        return False
    inner = trade.get("result") if isinstance(trade.get("result"), dict) else {}
    if str((inner or {}).get("error") or "").strip():
        return False
    if str((inner or {}).get("status") or "").lower() in ("error", "rejected", "unsupported"):
        return False
    return True


def place_entry_order(signal: Signal, config: WatcherConfig) -> dict[str, Any]:
    """Place an entry order for ``signal`` when auto-trade gates pass."""
    ok, why = should_auto_trade(signal, config)
    if not ok:
        return {"status": "skipped", "reason": why}

    from src.trading.service import place_order

    qty = float(config.auto_trade_quantity)
    side = "buy" if signal.side is Side.BUY else "sell"
    try:
        result = place_order(
            symbol=signal.instrument,
            profile_id=_profile_id(config),
            side=side,
            quantity=qty,
            order_type="market",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("auto-trade entry failed for %s", signal.instrument)
        return {"status": "error", "error": str(exc)}

    status = str(result.get("status") or "").lower()
    if status in ("error", "rejected", "unsupported") or result.get("error"):
        logger.warning(
            "auto-trade entry rejected %s %s: %s",
            side,
            signal.instrument,
            result.get("error") or result,
        )
        return {
            "status": "error",
            "side": side,
            "quantity": qty,
            "confidence": signal.confidence,
            "result": result,
            "error": result.get("error") or status,
        }

    logger.info(
        "AUTO-TRADE ENTRY %s %s qty=%s conf=%.1f → %s",
        side.upper(),
        signal.instrument,
        qty,
        signal.confidence,
        result.get("order_id") or status or "ok",
    )
    return {
        "status": status or "ok",
        "side": side,
        "quantity": qty,
        "confidence": signal.confidence,
        "result": result,
    }



def place_exit_order(
    position: OpenPosition,
    config: WatcherConfig,
    *,
    reason: str,
) -> dict[str, Any]:
    """Close an auto-traded position with an opposite-side market order."""
    if not config.auto_trade_enabled or config.dry_run:
        return {"status": "skipped", "reason": "auto_trade_disabled_or_dry_run"}
    if not getattr(position, "auto_traded", False):
        return {"status": "skipped", "reason": "position_not_auto_traded"}

    from src.trading.service import place_order

    qty = float(getattr(position, "quantity", 0) or config.auto_trade_quantity or 0)
    if qty <= 0:
        return {"status": "skipped", "reason": "quantity_missing"}

    # Opposite side to flatten.
    side = "sell" if position.side is Side.BUY else "buy"
    try:
        result = place_order(
            symbol=position.instrument,
            profile_id=_profile_id(config),
            side=side,
            quantity=qty,
            order_type="market",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("auto-trade exit failed for %s", position.instrument)
        return {"status": "error", "error": str(exc), "reason": reason}

    logger.info(
        "AUTO-TRADE EXIT %s %s qty=%s reason=%s → %s",
        side.upper(),
        position.instrument,
        qty,
        reason,
        result.get("order_id") or result.get("status") or "ok",
    )
    return {
        "status": str(result.get("status") or "ok"),
        "side": side,
        "quantity": qty,
        "exit_reason": reason,
        "result": result,
    }
