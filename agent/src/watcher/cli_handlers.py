"""CLI: ``vibe-trading watch {start,status,stop,once,config}``."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
from pathlib import Path

from src.watcher.config import WatcherConfig


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "watch",
        help="Autonomous Indian-market watcher (Upstox → high-probability alerts + optional auto-trade)",
    )
    sub = parser.add_subparsers(dest="watch_command")

    start = sub.add_parser("start", help="Start the always-on market watcher")
    start.add_argument("--dry-run", action="store_true", help="Log signals; skip Telegram / auto-trade")
    start.add_argument("--feed", choices=["auto", "poll", "websocket"], default=None)
    start.add_argument("--max-instruments", type=int, default=None)
    start.add_argument("--min-confidence", type=float, default=None)
    start.add_argument("--poll-interval", type=float, default=None)
    start.add_argument("--auto-trade", action="store_true", help="Place orders when confidence ≥ threshold")
    start.add_argument("--quantity", type=float, default=None, help="Auto-trade quantity (default 1)")
    start.add_argument("--symbols", default=None, help="Comma-separated watch-only symbols (e.g. RELIANCE)")
    start.add_argument("--foreground", action="store_true", help="Run in foreground (default)")
    start.add_argument("-v", "--verbose", action="store_true")

    sub.add_parser("status", help="Show watcher status / learning summary")
    sub.add_parser("stop", help="Stop a background watcher (via pid file)")

    once = sub.add_parser("once", help="Single scan cycle then exit (for testing)")
    once.add_argument("--dry-run", action="store_true", default=True)
    once.add_argument("--max-instruments", type=int, default=30)
    once.add_argument("--symbols", default=None, help="Comma-separated watch-only symbols")
    once.add_argument("-v", "--verbose", action="store_true")

    cfg = sub.add_parser("config", help="Show or write watcher config")
    cfg.add_argument("--set-telegram-token", default=None)
    cfg.add_argument("--set-telegram-chat-id", default=None)
    cfg.add_argument("--min-confidence", type=float, default=None)
    cfg.add_argument("--enable-auto-trade", action="store_true")
    cfg.add_argument("--disable-auto-trade", action="store_true")
    cfg.add_argument("--quantity", type=float, default=None)
    cfg.add_argument("--profile", default=None, help="Trading profile id (e.g. upstox-paper-trade)")
    cfg.add_argument("--symbols", default=None, help="Comma-separated watch-only symbols")
    cfg.add_argument("--auto-trade-symbols", default=None, help="Comma-separated symbols allowed to auto-trade")
    cfg.add_argument("--include-stock-options", action="store_true")
    cfg.add_argument("--include-index-options", action="store_true")
    cfg.add_argument("--print", action="store_true")


def dispatch(args: argparse.Namespace) -> int:
    cmd = getattr(args, "watch_command", None)
    if not cmd:
        print("watch requires a subcommand: start | status | stop | once | config", file=sys.stderr)
        return 2
    if cmd == "start":
        return _cmd_start(args)
    if cmd == "status":
        return _cmd_status()
    if cmd == "stop":
        return _cmd_stop()
    if cmd == "once":
        return _cmd_once(args)
    if cmd == "config":
        return _cmd_config(args)
    print(f"unknown watch command: {cmd}", file=sys.stderr)
    return 2


def _parse_symbols(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip().upper() for part in str(raw).split(",") if part.strip()]


def _apply_overrides(config: WatcherConfig, args: argparse.Namespace) -> WatcherConfig:
    if getattr(args, "dry_run", False):
        config.dry_run = True
    if getattr(args, "feed", None):
        config.feed_mode = args.feed
    if getattr(args, "max_instruments", None):
        config.max_instruments = int(args.max_instruments)
    if getattr(args, "min_confidence", None):
        config.min_confidence = float(args.min_confidence)
    if getattr(args, "poll_interval", None):
        config.poll_interval_seconds = float(args.poll_interval)
    if getattr(args, "auto_trade", False):
        config.auto_trade_enabled = True
    if getattr(args, "quantity", None) is not None:
        config.auto_trade_quantity = float(args.quantity)
    symbols = _parse_symbols(getattr(args, "symbols", None))
    if symbols:
        config.watch_only_symbols = symbols
        if not config.auto_trade_symbols:
            config.auto_trade_symbols = list(symbols)
    return config


def _cmd_start(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
    )
    config = _apply_overrides(WatcherConfig.load(), args)
    from src.watcher.engine import WatcherEngine

    engine = WatcherEngine(config)
    print(
        "Starting watcher · "
        f"feed={config.feed_mode} · min_confidence={config.min_confidence} · "
        f"dry_run={config.dry_run} · auto_trade={config.auto_trade_enabled} · "
        f"qty={config.auto_trade_quantity} · symbols={config.watch_only_symbols or 'default'}"
    )
    try:
        engine.start(blocking=True)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _cmd_once(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    config = _apply_overrides(WatcherConfig.load(), args)
    config.dry_run = True
    config.max_instruments = min(config.max_instruments, int(getattr(args, "max_instruments", 30) or 30))
    from src.watcher.engine import WatcherEngine
    from src.watcher.market_filter import build_market_context
    from src.watcher.universe import build_universe

    engine = WatcherEngine(config)
    engine.universe = build_universe(config)
    print(f"Once-scan universe={len(engine.universe)}")
    engine._seed_history()
    engine.market_ctx = build_market_context(engine._load_bars)
    engine.rest.poll(engine.universe)
    # Force analysis on each instrument once.
    for inst in engine.universe:
        engine._analyze(inst)
    print(json.dumps(engine.stats, indent=2))
    print(json.dumps(engine.learning.summary(), indent=2))
    return 0


def _cmd_status() -> int:
    config = WatcherConfig.load()
    from src.watcher.learning import LearningEngine
    from src.watcher.storage import WatcherStore

    store = WatcherStore(config.state_dir() / "watcher.db")
    learning = LearningEngine(store)
    pid_path = config.state_dir() / "watcher.pid"
    running = False
    pid = None
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip().splitlines()[-1])
            os.kill(pid, 0)
            running = True
        except Exception:
            running = False
    print(
        json.dumps(
            {
                "running": running,
                "pid": pid,
                "state_dir": str(config.state_dir()),
                "learning": learning.summary(),
                "min_confidence": config.min_confidence,
                "auto_trade_enabled": config.auto_trade_enabled,
                "auto_trade_quantity": config.auto_trade_quantity,
                "watch_only_symbols": config.watch_only_symbols,
                "telegram_configured": bool(config.telegram_bot_token and config.telegram_chat_id),
            },
            indent=2,
        )
    )
    return 0


def _cmd_stop() -> int:
    config = WatcherConfig.load()
    pid_path = config.state_dir() / "watcher.pid"
    if not pid_path.exists():
        print("watcher not running (no pid file)")
        return 0
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip().splitlines()[-1])
        os.kill(pid, signal.SIGTERM)
        print(f"sent SIGTERM to {pid}")
    except Exception as exc:
        print(f"stop failed: {exc}", file=sys.stderr)
        return 1
    try:
        pid_path.unlink()
    except OSError:
        pass
    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    config = WatcherConfig.load()
    changed = False
    if args.set_telegram_token:
        config.telegram_bot_token = args.set_telegram_token
        changed = True
    if args.set_telegram_chat_id:
        config.telegram_chat_id = str(args.set_telegram_chat_id)
        changed = True
    if args.min_confidence is not None:
        config.min_confidence = float(args.min_confidence)
        changed = True
    if getattr(args, "enable_auto_trade", False):
        config.auto_trade_enabled = True
        changed = True
    if getattr(args, "disable_auto_trade", False):
        config.auto_trade_enabled = False
        changed = True
    if getattr(args, "quantity", None) is not None:
        config.auto_trade_quantity = float(args.quantity)
        changed = True
    if getattr(args, "profile", None):
        config.auto_trade_profile_id = str(args.profile).strip()
        changed = True
    symbols = _parse_symbols(getattr(args, "symbols", None))
    if symbols:
        config.watch_only_symbols = symbols
        changed = True
    auto_syms = _parse_symbols(getattr(args, "auto_trade_symbols", None))
    if auto_syms:
        config.auto_trade_symbols = auto_syms
        changed = True
    if getattr(args, "include_stock_options", False):
        config.include_stock_options = True
        changed = True
    if getattr(args, "include_index_options", False):
        config.include_index_options = True
        changed = True
    if changed:
        path = config.save()
        print(f"wrote {path}")
    from dataclasses import asdict

    print(json.dumps(asdict(config), indent=2))
    return 0
