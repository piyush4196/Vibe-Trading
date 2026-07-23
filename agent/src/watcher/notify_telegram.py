"""Telegram notifier — Markdown + inline buttons via Bot API (httpx)."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import httpx

from src.watcher.config import WatcherConfig
from src.watcher.models import Signal

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Sends watcher alerts without requiring the full IM channel stack."""

    def __init__(self, config: WatcherConfig):
        self.config = config

    @property
    def enabled(self) -> bool:
        return bool(
            self.config.telegram_enabled
            and self.config.telegram_bot_token
            and self.config.telegram_chat_id
            and not self.config.dry_run
        )

    def send_signal(self, signal: Signal) -> dict[str, Any]:
        text = signal.format_telegram()
        buttons = [
            [
                {"text": "Open Chart", "url": _chart_url(signal)},
                {"text": "Show Reasoning", "callback_data": f"reason:{signal.signal_id[:20]}"},
            ],
            [
                {"text": "Ignore", "callback_data": f"ignore:{signal.signal_id[:20]}"},
                {"text": "Paper Trade", "callback_data": f"paper:{signal.signal_id[:20]}"},
                {"text": "Execute Trade", "callback_data": f"exec:{signal.signal_id[:20]}"},
            ],
        ]
        return self.send_markdown(text, buttons=buttons)

    def send_markdown(self, text: str, *, buttons: list[list[dict[str, str]]] | None = None) -> dict[str, Any]:
        if self.config.dry_run or not self.config.telegram_bot_token:
            logger.info("[dry-run telegram]\n%s", text)
            return {"status": "dry_run"}
        if not self.config.telegram_chat_id:
            return {"status": "error", "error": "telegram_chat_id not set"}

        url = f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": self.config.telegram_chat_id,
            "text": text[:4000],
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.post(url, json=payload)
            data = resp.json()
            if not data.get("ok"):
                # Retry as plain text if markdown fails.
                payload.pop("parse_mode", None)
                with httpx.Client(timeout=20.0) as client:
                    resp = client.post(url, json=payload)
                data = resp.json()
            return {"status": "ok" if data.get("ok") else "error", "response": data}
        except Exception as exc:  # noqa: BLE001
            logger.exception("telegram send failed")
            return {"status": "error", "error": str(exc)}


def _chart_url(signal: Signal) -> str:
    # TradingView-style deep link (NSE symbols).
    symbol = signal.instrument.replace(" ", "").replace("-", "")
    exchange = "NSE" if signal.market in ("NSE", "CDS") else signal.market
    return f"https://www.tradingview.com/chart/?symbol={quote(exchange + ':' + symbol)}"
