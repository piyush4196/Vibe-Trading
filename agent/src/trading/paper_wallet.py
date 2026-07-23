"""Local paper-trading wallet — virtual cash for simulated fills.

Persists under ``~/.vibe-trading/paper_wallet.json``. Used by local-sim
connectors (Upstox / Dhan / Shoonya paper) so you can deposit play money,
run strategy logic, and see cash / equity / P&L before risking real capital.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from src.config.paths import get_runtime_root

Side = Literal["buy", "sell"]


class PaperWalletError(RuntimeError):
    """User-facing paper wallet failure."""


@dataclass
class LedgerEntry:
    entry_id: str
    kind: str  # deposit | withdraw | fill | reset
    amount: float
    balance_after: float
    ts: str
    note: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class PaperPosition:
    symbol: str
    quantity: float
    avg_price: float
    side: Side = "buy"  # long book; quantity > 0 means long
    instrument_key: str = ""
    updated_at: str = ""
    last_price: float = 0.0

    @property
    def market_value(self) -> float:
        px = self.last_price or self.avg_price
        return self.quantity * px

    @property
    def cost_basis(self) -> float:
        return self.quantity * self.avg_price

    @property
    def unrealized_pnl(self) -> float:
        return self.market_value - self.cost_basis


@dataclass
class PaperWallet:
    currency: str = "INR"
    cash: float = 0.0
    realized_pnl: float = 0.0
    starting_cash: float = 0.0
    positions: dict[str, PaperPosition] = field(default_factory=dict)
    ledger: list[LedgerEntry] = field(default_factory=list)
    updated_at: str = ""

    def equity(self) -> float:
        return round(self.cash + sum(p.market_value for p in self.positions.values()), 4)

    def unrealized_pnl(self) -> float:
        return round(sum(p.unrealized_pnl for p in self.positions.values()), 4)

    def total_pnl(self) -> float:
        return round(self.realized_pnl + self.unrealized_pnl(), 4)

    def total_deposited(self) -> float:
        return round(
            sum(e.amount for e in self.ledger if e.kind == "deposit")
            - sum(e.amount for e in self.ledger if e.kind == "withdraw"),
            4,
        )


_lock = threading.Lock()


def wallet_path() -> Path:
    return get_runtime_root() / "paper_wallet.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _position_from_dict(raw: dict[str, Any]) -> PaperPosition:
    return PaperPosition(
        symbol=str(raw.get("symbol") or "").upper(),
        quantity=float(raw.get("quantity") or 0),
        avg_price=float(raw.get("avg_price") or 0),
        side=str(raw.get("side") or "buy").lower(),  # type: ignore[arg-type]
        instrument_key=str(raw.get("instrument_key") or ""),
        updated_at=str(raw.get("updated_at") or ""),
        last_price=float(raw.get("last_price") or 0),
    )


def _entry_from_dict(raw: dict[str, Any]) -> LedgerEntry:
    return LedgerEntry(
        entry_id=str(raw.get("entry_id") or _new_id("le")),
        kind=str(raw.get("kind") or ""),
        amount=float(raw.get("amount") or 0),
        balance_after=float(raw.get("balance_after") or 0),
        ts=str(raw.get("ts") or _utc_now()),
        note=str(raw.get("note") or ""),
        meta=dict(raw.get("meta") or {}),
    )


def load_wallet() -> PaperWallet:
    path = wallet_path()
    if not path.exists():
        return PaperWallet(updated_at=_utc_now())
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaperWalletError(f"invalid paper wallet at {path}: {exc}") from exc
    positions_raw = data.get("positions") or {}
    positions: dict[str, PaperPosition] = {}
    if isinstance(positions_raw, dict):
        for key, val in positions_raw.items():
            if isinstance(val, dict):
                pos = _position_from_dict(val)
                positions[str(key).upper()] = pos
    ledger = [_entry_from_dict(e) for e in (data.get("ledger") or []) if isinstance(e, dict)]
    return PaperWallet(
        currency=str(data.get("currency") or "INR"),
        cash=float(data.get("cash") or 0),
        realized_pnl=float(data.get("realized_pnl") or 0),
        starting_cash=float(data.get("starting_cash") or 0),
        positions=positions,
        ledger=ledger[-500:],
        updated_at=str(data.get("updated_at") or _utc_now()),
    )


def save_wallet(wallet: PaperWallet) -> Path:
    path = wallet_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    wallet.updated_at = _utc_now()
    payload = {
        "currency": wallet.currency,
        "cash": round(wallet.cash, 4),
        "realized_pnl": round(wallet.realized_pnl, 4),
        "starting_cash": round(wallet.starting_cash, 4),
        "positions": {
            k: asdict(v) for k, v in wallet.positions.items() if v.quantity > 1e-12
        },
        "ledger": [asdict(e) for e in wallet.ledger[-500:]],
        "updated_at": wallet.updated_at,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def snapshot(wallet: PaperWallet | None = None) -> dict[str, Any]:
    w = wallet or load_wallet()
    deposited = w.total_deposited()
    equity = w.equity()
    unrealized = w.unrealized_pnl()
    total_pnl = w.total_pnl()
    pnl_pct = (total_pnl / deposited * 100.0) if deposited > 0 else 0.0
    return {
        "currency": w.currency,
        "cash": round(w.cash, 4),
        "equity": equity,
        "buying_power": round(w.cash, 4),
        "starting_cash": round(w.starting_cash, 4),
        "total_deposited": deposited,
        "realized_pnl": round(w.realized_pnl, 4),
        "unrealized_pnl": unrealized,
        "total_pnl": total_pnl,
        "total_pnl_pct": round(pnl_pct, 4),
        "open_positions": len([p for p in w.positions.values() if p.quantity > 0]),
        "positions": [
            {
                **asdict(p),
                "market_value": round(p.market_value, 4),
                "cost_basis": round(p.cost_basis, 4),
                "unrealized_pnl": round(p.unrealized_pnl, 4),
            }
            for p in w.positions.values()
            if p.quantity > 0
        ],
        "ledger": [asdict(e) for e in reversed(w.ledger[-40:])],
        "updated_at": w.updated_at,
        "wallet_path": str(wallet_path()),
    }


def deposit(amount: float, *, note: str = "", currency: str | None = None) -> dict[str, Any]:
    amt = float(amount)
    if amt <= 0:
        raise PaperWalletError("deposit amount must be positive")
    with _lock:
        w = load_wallet()
        if currency:
            w.currency = str(currency).upper()
        w.cash = round(w.cash + amt, 4)
        if w.starting_cash <= 0 and len([e for e in w.ledger if e.kind == "deposit"]) == 0:
            w.starting_cash = amt
        entry = LedgerEntry(
            entry_id=_new_id("dep"),
            kind="deposit",
            amount=amt,
            balance_after=w.cash,
            ts=_utc_now(),
            note=note or "Paper deposit",
        )
        w.ledger.append(entry)
        save_wallet(w)
        return snapshot(w)


def withdraw(amount: float, *, note: str = "") -> dict[str, Any]:
    amt = float(amount)
    if amt <= 0:
        raise PaperWalletError("withdraw amount must be positive")
    with _lock:
        w = load_wallet()
        if amt > w.cash + 1e-9:
            raise PaperWalletError(f"insufficient cash: have {w.cash}, need {amt}")
        w.cash = round(w.cash - amt, 4)
        w.ledger.append(
            LedgerEntry(
                entry_id=_new_id("wd"),
                kind="withdraw",
                amount=amt,
                balance_after=w.cash,
                ts=_utc_now(),
                note=note or "Paper withdraw",
            )
        )
        save_wallet(w)
        return snapshot(w)


def reset_wallet(*, keep_currency: bool = True) -> dict[str, Any]:
    with _lock:
        old = load_wallet()
        currency = old.currency if keep_currency else "INR"
        w = PaperWallet(currency=currency, updated_at=_utc_now())
        w.ledger.append(
            LedgerEntry(
                entry_id=_new_id("rst"),
                kind="reset",
                amount=0,
                balance_after=0,
                ts=_utc_now(),
                note="Paper wallet reset",
            )
        )
        save_wallet(w)
        return snapshot(w)


def mark_prices(prices: dict[str, float]) -> dict[str, Any]:
    """Update last_price for open positions (symbol or instrument_key → price)."""
    lookup = {str(k).upper(): float(v) for k, v in prices.items() if float(v) > 0}
    if not lookup:
        return snapshot()
    with _lock:
        w = load_wallet()
        changed = False
        for pos in w.positions.values():
            px = lookup.get(pos.symbol.upper()) or lookup.get(pos.instrument_key.upper())
            if px is not None:
                pos.last_price = float(px)
                pos.updated_at = _utc_now()
                changed = True
        if changed:
            save_wallet(w)
        return snapshot(w)


def apply_fill(
    *,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    instrument_key: str = "",
    order_id: str = "",
    broker: str = "paper",
) -> dict[str, Any]:
    """Apply a simulated fill against the paper wallet.

    BUY debits cash and opens/adds a long. SELL reduces a long and realizes PnL
    (shorting is not supported in v1 — selling without inventory fails).
    """
    clean = str(symbol or "").strip().upper()
    if not clean:
        raise PaperWalletError("symbol is required")
    side_token = str(side or "").strip().lower()
    if side_token not in ("buy", "sell"):
        raise PaperWalletError("side must be buy or sell")
    qty = float(quantity)
    px = float(price)
    if qty <= 0 or px <= 0:
        raise PaperWalletError("quantity and price must be positive")

    notional = round(qty * px, 4)
    with _lock:
        w = load_wallet()
        key = clean
        pos = w.positions.get(key)

        if side_token == "buy":
            if notional > w.cash + 1e-9:
                raise PaperWalletError(
                    f"insufficient paper cash: need {notional} {w.currency}, have {w.cash}"
                )
            w.cash = round(w.cash - notional, 4)
            if pos is None or pos.quantity <= 0:
                w.positions[key] = PaperPosition(
                    symbol=clean,
                    quantity=qty,
                    avg_price=px,
                    side="buy",
                    instrument_key=instrument_key or clean,
                    updated_at=_utc_now(),
                    last_price=px,
                )
            else:
                new_qty = pos.quantity + qty
                pos.avg_price = round(
                    (pos.avg_price * pos.quantity + px * qty) / new_qty, 6
                )
                pos.quantity = new_qty
                pos.last_price = px
                pos.instrument_key = instrument_key or pos.instrument_key
                pos.updated_at = _utc_now()
        else:
            if pos is None or pos.quantity <= 1e-12:
                raise PaperWalletError(f"no paper position to sell for {clean}")
            if qty > pos.quantity + 1e-9:
                raise PaperWalletError(
                    f"sell qty {qty} exceeds position {pos.quantity} for {clean}"
                )
            realized = round((px - pos.avg_price) * qty, 4)
            w.realized_pnl = round(w.realized_pnl + realized, 4)
            w.cash = round(w.cash + notional, 4)
            pos.quantity = round(pos.quantity - qty, 6)
            pos.last_price = px
            pos.updated_at = _utc_now()
            if pos.quantity <= 1e-12:
                del w.positions[key]

        w.ledger.append(
            LedgerEntry(
                entry_id=_new_id("fill"),
                kind="fill",
                amount=notional if side_token == "sell" else -notional,
                balance_after=w.cash,
                ts=_utc_now(),
                note=f"{side_token.upper()} {qty} {clean} @ {px}",
                meta={
                    "symbol": clean,
                    "side": side_token,
                    "quantity": qty,
                    "price": px,
                    "order_id": order_id,
                    "broker": broker,
                    "instrument_key": instrument_key,
                },
            )
        )
        save_wallet(w)
        out = snapshot(w)
        out["fill"] = {
            "symbol": clean,
            "side": side_token,
            "quantity": qty,
            "price": px,
            "notional": notional,
            "order_id": order_id,
        }
        return out
