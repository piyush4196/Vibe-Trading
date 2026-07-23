"""Autonomous Indian-market watcher — always-on desk analyst (not a chatbot)."""

from __future__ import annotations

__all__ = ["WatcherEngine", "WatcherConfig"]

from src.watcher.config import WatcherConfig
from src.watcher.engine import WatcherEngine
