"""Watcher configuration — persisted under ``~/.vibe-trading/watcher/``."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root


@dataclass
class WatcherConfig:
    """Runtime knobs for the autonomous market watcher."""

    feed_mode: str = "auto"  # auto | websocket | poll
    poll_interval_seconds: float = 15.0
    websocket_mode: str = "full"

    include_indices: bool = True
    include_nifty50: bool = True
    include_nifty_next50: bool = True
    include_fno_stocks: bool = True
    include_index_futures: bool = True
    include_stock_futures: bool = True
    include_index_options: bool = False
    include_stock_options: bool = False
    include_mcx: bool = True
    include_currency: bool = True
    max_instruments: int = 200
    extra_symbols: list[str] = field(default_factory=list)

    min_confidence: float = 80.0
    preferred_rr: float = 3.0
    min_rr: float = 2.0
    lookback_bars: int = 250

    alert_cooldown_seconds: int = 1800
    confidence_bump_to_resend: float = 5.0

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_enabled: bool = True

    dry_run: bool = False
    state_dirname: str = "watcher"

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None = None) -> "WatcherConfig":
        payload = dict(data or {})
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        clean = {k: v for k, v in payload.items() if k in known}
        if "extra_symbols" in clean and clean["extra_symbols"] is None:
            clean["extra_symbols"] = []
        return cls(**clean)

    def state_dir(self) -> Path:
        path = get_runtime_root() / self.state_dirname
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save(self) -> Path:
        path = self.state_dir() / "config.json"
        path.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        return path

    @classmethod
    def load(cls) -> "WatcherConfig":
        path = get_runtime_root() / "watcher" / "config.json"
        if not path.exists():
            return cls()
        try:
            return cls.from_mapping(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError):
            return cls()
