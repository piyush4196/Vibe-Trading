"""Upstox Market Data Feed V3 WebSocket collector.

Binary protobuf frames are decoded when ``protobuf`` + generated stubs are
available; otherwise the collector still maintains the authorized connection /
subscription and relies on the REST poller as the analysis feed (auto mode).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from src.trading.connectors.upstox import sdk as upstox_sdk
from src.watcher.models import Tick, WatchInstrument

logger = logging.getLogger(__name__)

WS_AUTHORIZE_URL = "https://api.upstox.com/v3/feed/market-data-feed"


class UpstoxWebSocketCollector:
    def __init__(
        self,
        config: upstox_sdk.UpstoxConfig | None = None,
        *,
        mode: str = "full",
        on_tick: Callable[[Tick], None] | None = None,
        on_market_info: Callable[[dict[str, str]], None] | None = None,
    ):
        self.cfg = config or upstox_sdk.load_config()
        self.mode = mode
        self.on_tick = on_tick
        self.on_market_info = on_market_info
        self._stop = asyncio.Event()
        self.segment_status: dict[str, str] = {}
        self.last_error: str | None = None
        self.connected = False

    def stop(self) -> None:
        self._stop.set()

    async def run(self, instruments: Iterable[WatchInstrument]) -> None:
        keys = [i.instrument_key for i in instruments]
        symbol_map = {i.instrument_key: i.symbol for i in instruments}
        try:
            import websockets
            from websockets.exceptions import ConnectionClosed
        except ImportError as exc:
            self.last_error = f"websockets not installed: {exc}"
            logger.error(self.last_error)
            return

        headers = {
            "Authorization": f"Bearer {self.cfg.access_token}",
            "Accept": "*/*",
        }
        try:
            async with websockets.connect(
                WS_AUTHORIZE_URL,
                additional_headers=headers,
                open_timeout=30,
                max_size=8 * 1024 * 1024,
            ) as ws:
                self.connected = True
                logger.info("Upstox WS connected; subscribing %d keys", len(keys))
                # Subscribe in chunks of 100 to respect payload size.
                for i in range(0, len(keys), 100):
                    chunk = keys[i : i + 100]
                    sub = {
                        "guid": uuid.uuid4().hex[:16],
                        "method": "sub",
                        "data": {"mode": self.mode, "instrumentKeys": chunk},
                    }
                    await ws.send(json.dumps(sub).encode("utf-8"))
                while not self._stop.is_set():
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    except asyncio.TimeoutError:
                        continue
                    except ConnectionClosed:
                        break
                    self._handle_message(msg, symbol_map)
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            logger.warning("Upstox WS error: %s", exc)
        finally:
            self.connected = False

    def _handle_message(self, msg: Any, symbol_map: dict[str, str]) -> None:
        # Text JSON (some gateways) or decoded dict after protobuf.
        payload: dict[str, Any] | None = None
        if isinstance(msg, (bytes, bytearray)):
            # Try UTF-8 JSON first; protobuf needs generated stubs.
            try:
                payload = json.loads(msg.decode("utf-8"))
            except Exception:
                payload = _try_decode_protobuf(bytes(msg))
        elif isinstance(msg, str):
            try:
                payload = json.loads(msg)
            except Exception:
                return
        elif isinstance(msg, dict):
            payload = msg
        if not payload:
            return

        if payload.get("type") == "market_info":
            status = ((payload.get("marketInfo") or {}).get("segmentStatus")) or {}
            self.segment_status = {str(k): str(v) for k, v in status.items()}
            if self.on_market_info:
                self.on_market_info(self.segment_status)
            return

        feeds = payload.get("feeds") or {}
        for key, body in feeds.items():
            tick = _feed_to_tick(key, body, symbol_map.get(key, key))
            if tick and self.on_tick:
                self.on_tick(tick)


def _feed_to_tick(instrument_key: str, body: dict[str, Any], symbol: str) -> Tick | None:
    ltpc = body.get("ltpc") or {}
    ltp = float(ltpc.get("ltp") or body.get("ltp") or 0)
    if not ltp:
        return None
    full = body.get("fullFeed") or body.get("marketFF") or body
    ohlc = full.get("marketOHLC") or full.get("ohlc") or {}
    # marketOHLC may be list of interval candles
    day = {}
    if isinstance(ohlc, dict):
        day = ohlc
    elif isinstance(ohlc, list) and ohlc:
        day = ohlc[-1] if isinstance(ohlc[-1], dict) else {}
    depth = full.get("marketLevel") or full.get("depth") or {}
    bid = ask = 0.0
    if isinstance(depth, dict):
        bids = depth.get("bid") or depth.get("buy") or []
        asks = depth.get("ask") or depth.get("sell") or []
        if bids:
            bid = float((bids[0] or {}).get("price") or (bids[0] or {}).get("p") or 0)
        if asks:
            ask = float((asks[0] or {}).get("price") or (asks[0] or {}).get("p") or 0)
    ltt = ltpc.get("ltt")
    try:
        ts = (
            datetime.fromtimestamp(float(ltt) / 1000.0, tz=timezone.utc)
            if ltt
            else datetime.now(timezone.utc)
        )
    except Exception:
        ts = datetime.now(timezone.utc)
    return Tick(
        instrument_key=instrument_key,
        symbol=symbol,
        ts=ts,
        ltp=ltp,
        volume=float(full.get("vtt") or full.get("vol") or ltpc.get("ltq") or 0),
        oi=float(full.get("oi") or 0),
        bid=bid,
        ask=ask,
        open=float(day.get("open") or day.get("o") or 0),
        high=float(day.get("high") or day.get("h") or 0),
        low=float(day.get("low") or day.get("l") or 0),
        close=float(day.get("close") or day.get("c") or ltp),
        prev_close=float(ltpc.get("cp") or 0),
        vwap=float(full.get("iv") or full.get("avgTradePrice") or ltp),
        raw=body if isinstance(body, dict) else {},
    )


def _try_decode_protobuf(data: bytes) -> dict[str, Any] | None:
    """Best-effort protobuf decode if optional generated module is present."""
    try:
        from src.watcher import upstox_pb2  # type: ignore
    except Exception:
        return None
    try:
        msg = upstox_pb2.FeedResponse()  # type: ignore[attr-defined]
        msg.ParseFromString(data)
        # Convert via JSON for a uniform handler path.
        from google.protobuf.json_format import MessageToDict

        return MessageToDict(msg, preserving_proto_field_name=True)
    except Exception:
        return None
