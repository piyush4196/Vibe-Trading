"""Domain models for the autonomous market watcher."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class HoldingType(str, Enum):
    INTRADAY = "INTRADAY"
    SWING = "SWING"
    POSITIONAL = "POSITIONAL"


class MarketSegment(str, Enum):
    INDEX = "INDEX"
    EQUITY = "EQUITY"
    FUTURE = "FUTURE"
    OPTION = "OPTION"
    COMMODITY = "COMMODITY"
    CURRENCY = "CURRENCY"


@dataclass(frozen=True)
class WatchInstrument:
    """A single instrument in the live universe."""

    symbol: str
    instrument_key: str
    market: str  # NSE / BSE / MCX / CDS
    segment: MarketSegment
    name: str = ""
    lot_size: int = 1
    tick_size: float = 0.05


@dataclass
class Tick:
    """Normalized live tick."""

    instrument_key: str
    symbol: str
    ts: datetime
    ltp: float
    volume: float = 0.0
    oi: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    bid_qty: float = 0.0
    ask_qty: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    prev_close: float = 0.0
    vwap: float = 0.0
    depth: list[dict[str, float]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Candle:
    """OHLCV(+OI) bar."""

    instrument_key: str
    timeframe: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    oi: float = 0.0
    vwap: float = 0.0
    closed: bool = True


@dataclass
class MarketContext:
    """Top-down market filter state."""

    india_vix: float | None = None
    nifty_trend: str = "neutral"  # bullish | bearish | neutral
    banknifty_trend: str = "neutral"
    advance_decline: float | None = None
    sector_rotation: dict[str, float] = field(default_factory=dict)
    breadth_score: float = 50.0  # 0-100
    notes: list[str] = field(default_factory=list)


@dataclass
class Signal:
    """High-probability trade opportunity."""

    signal_id: str
    instrument: str
    instrument_key: str
    market: str
    side: Side
    strategy: str
    entry: float
    stop_loss: float
    target_1: float
    target_2: float
    target_3: float
    confidence: float
    risk_reward: float
    holding_type: HoldingType
    expected_holding: str
    reasons: list[str]
    timeframe_alignment: dict[str, str] = field(default_factory=dict)
    indicators_snapshot: dict[str, Any] = field(default_factory=dict)
    market_context: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    status: str = "open"  # open | target_hit | stopped | exited | ignored

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["side"] = self.side.value
        data["holding_type"] = self.holding_type.value
        data["created_at"] = self.created_at.isoformat()
        return data

    def format_telegram(self) -> str:
        emoji = "🚀" if self.side is Side.BUY else "🔻"
        lines = [
            f"{emoji} *HIGH PROBABILITY {self.side.value}*",
            "",
            f"*Instrument*\n`{self.instrument}`",
            f"*Market*\n`{self.market}`",
            f"*Confidence*\n`{self.confidence:.0f}%`",
            "",
            f"*Entry*\n`{self.entry:.2f}`",
            f"*Stop Loss*\n`{self.stop_loss:.2f}`",
            f"*Target 1*\n`{self.target_1:.2f}`",
            f"*Target 2*\n`{self.target_2:.2f}`",
            f"*Target 3*\n`{self.target_3:.2f}`",
            "",
            f"*Risk Reward*\n`1:{self.risk_reward:.1f}`",
            f"*Strategy*\n`{self.strategy}`",
            f"*Holding*\n`{self.holding_type.value}` · `{self.expected_holding}`",
            "",
            "*Reasons*",
        ]
        for reason in self.reasons[:12]:
            lines.append(f"• {reason}")
        return "\n".join(lines)


@dataclass
class OpenPosition:
    """Paper / tracked position spawned from a signal."""

    signal_id: str
    instrument: str
    instrument_key: str
    side: Side
    entry: float
    stop_loss: float
    targets: list[float]
    trailing_stop: float | None = None
    opened_at: datetime = field(default_factory=utc_now)
    status: str = "open"
    peak_price: float = 0.0
    trough_price: float = 0.0
