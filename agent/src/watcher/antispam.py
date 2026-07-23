"""Anti-spam gate — quality over quantity."""

from __future__ import annotations

from datetime import datetime, timezone

from src.watcher.config import WatcherConfig
from src.watcher.models import Signal
from src.watcher.storage import WatcherStore


def should_emit(signal: Signal, store: WatcherStore, config: WatcherConfig) -> tuple[bool, str]:
    """Return whether Telegram/notify should fire for ``signal``."""
    prev = store.get_alert_state(signal.instrument_key, signal.side.value)
    if not prev:
        return True, "first_alert"

    try:
        last_at = datetime.fromisoformat(str(prev["last_alert_at"]))
        if last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=timezone.utc)
    except Exception:
        return True, "bad_prior_timestamp"

    age = (datetime.now(timezone.utc) - last_at).total_seconds()
    if age < config.alert_cooldown_seconds:
        # Allow only on meaningful upgrades.
        conf_bump = signal.confidence - float(prev.get("last_confidence") or 0)
        entry_chg = abs(signal.entry - float(prev.get("last_entry") or signal.entry)) / max(signal.entry, 1e-9)
        stop_chg = abs(signal.stop_loss - float(prev.get("last_stop") or signal.stop_loss)) / max(signal.entry, 1e-9)
        tgt_chg = abs(signal.target_1 - float(prev.get("last_target") or signal.target_1)) / max(signal.entry, 1e-9)
        if conf_bump >= config.confidence_bump_to_resend:
            return True, f"confidence_up_{+conf_bump:.1f}"
        if entry_chg > 0.01 or stop_chg > 0.01 or tgt_chg > 0.015:
            return True, "levels_changed"
        return False, f"cooldown_{int(config.alert_cooldown_seconds - age)}s"
    return True, "cooldown_expired"
