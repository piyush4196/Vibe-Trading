"""Home dashboard summary — P&L, orders capability, recent logs.

Mounted by ``agent/api_server.py`` via ``register_dashboard_routes(app, ...)``.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field

from src.config.paths import get_runtime_root

AuthDep = Callable[..., Awaitable[Any] | Any]


class PeriodPnl(BaseModel):
    label: str
    pnl_pct: float = 0.0
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0


class DailyPnlPoint(BaseModel):
    date: str
    pnl_pct: float = 0.0
    trades: int = 0


class MonthlyPnlPoint(BaseModel):
    month: str
    pnl_pct: float = 0.0
    trades: int = 0


class RecentTrade(BaseModel):
    signal_id: str
    closed_at: Optional[str] = None
    pnl_pct: float = 0.0
    exit_reason: str = ""
    instrument: str = ""
    side: str = ""


class AuditLogEntry(BaseModel):
    audit_id: str = ""
    ts: str = ""
    kind: str = ""
    outcome: str = ""
    server: str = ""
    intent: Optional[str] = None
    error: Optional[str] = None


class RecentRun(BaseModel):
    run_id: str
    created_at: str = ""
    status: str = ""
    prompt: str = ""
    total_return: Optional[float] = None
    sharpe: Optional[float] = None


class OrdersCapability(BaseModel):
    can_place_orders: bool
    paper_supported: bool
    live_supported: bool
    upstox_configured: bool
    upstox_live_orders: bool = False
    note: str = ""


class PaperWalletSummary(BaseModel):
    currency: str = "INR"
    cash: float = 0.0
    equity: float = 0.0
    total_deposited: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    open_positions: int = 0


class DashboardSummary(BaseModel):
    generated_at: str
    today: PeriodPnl
    week: PeriodPnl
    month: PeriodPnl
    all_time: PeriodPnl
    daily: list[DailyPnlPoint] = Field(default_factory=list)
    monthly: list[MonthlyPnlPoint] = Field(default_factory=list)
    recent_trades: list[RecentTrade] = Field(default_factory=list)
    recent_audit: list[AuditLogEntry] = Field(default_factory=list)
    recent_runs: list[RecentRun] = Field(default_factory=list)
    open_positions: int = 0
    orders: OrdersCapability
    paper_wallet: PaperWalletSummary = Field(default_factory=PaperWalletSummary)
    sources: dict[str, str] = Field(default_factory=dict)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        raw = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _period_from_outcomes(
    outcomes: list[dict[str, Any]],
    *,
    start: datetime | None,
    label: str,
) -> PeriodPnl:
    pnl = 0.0
    trades = 0
    wins = 0
    losses = 0
    for row in outcomes:
        closed = _parse_iso(str(row.get("closed_at") or ""))
        if start is not None and (closed is None or closed < start):
            continue
        trades += 1
        pct = float(row.get("pnl_pct") or 0.0)
        pnl += pct
        if pct > 0:
            wins += 1
        elif pct < 0:
            losses += 1
    win_rate = (wins / trades * 100.0) if trades else 0.0
    return PeriodPnl(
        label=label,
        pnl_pct=round(pnl, 4),
        trades=trades,
        wins=wins,
        losses=losses,
        win_rate=round(win_rate, 2),
    )


def _load_watcher_outcomes(limit: int = 2000) -> list[dict[str, Any]]:
    db = get_runtime_root() / "watcher" / "watcher.db"
    if not db.exists():
        # Also accept default engine path if configured differently.
        alt = get_runtime_root() / "watcher" / "state.db"
        db = alt if alt.exists() else db
    if not db.exists():
        return []
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM outcomes ORDER BY closed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


def _count_open_positions() -> int:
    db = get_runtime_root() / "watcher" / "watcher.db"
    if not db.exists():
        return 0
    try:
        conn = sqlite3.connect(str(db))
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM positions WHERE status='open'"
        ).fetchone()
        conn.close()
        return int(row[0] if row else 0)
    except sqlite3.Error:
        return 0


def _instrument_from_outcome(row: dict[str, Any]) -> tuple[str, str]:
    payload_raw = row.get("payload") or "{}"
    try:
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else (payload_raw or {})
    except json.JSONDecodeError:
        payload = {}
    instrument = str(payload.get("instrument") or payload.get("symbol") or "")
    side = str(payload.get("side") or "")
    return instrument, side


def _daily_series(outcomes: list[dict[str, Any]], days: int = 30) -> list[DailyPnlPoint]:
    today = datetime.now(timezone.utc).date()
    buckets: dict[date, list[float]] = defaultdict(list)
    for row in outcomes:
        closed = _parse_iso(str(row.get("closed_at") or ""))
        if closed is None:
            continue
        d = closed.date()
        if (today - d).days > days - 1:
            continue
        buckets[d].append(float(row.get("pnl_pct") or 0.0))
    out: list[DailyPnlPoint] = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        vals = buckets.get(d, [])
        out.append(
            DailyPnlPoint(
                date=d.isoformat(),
                pnl_pct=round(sum(vals), 4),
                trades=len(vals),
            )
        )
    return out


def _monthly_series(outcomes: list[dict[str, Any]], months: int = 12) -> list[MonthlyPnlPoint]:
    now = datetime.now(timezone.utc)
    buckets: dict[str, list[float]] = defaultdict(list)
    for row in outcomes:
        closed = _parse_iso(str(row.get("closed_at") or ""))
        if closed is None:
            continue
        key = f"{closed.year:04d}-{closed.month:02d}"
        buckets[key].append(float(row.get("pnl_pct") or 0.0))
    out: list[MonthlyPnlPoint] = []
    y, m = now.year, now.month
    for _ in range(months):
        key = f"{y:04d}-{m:02d}"
        vals = buckets.get(key, [])
        out.append(
            MonthlyPnlPoint(
                month=key,
                pnl_pct=round(sum(vals), 4),
                trades=len(vals),
            )
        )
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    out.reverse()
    return out


def _recent_audit(limit: int = 25) -> list[AuditLogEntry]:
    path = get_runtime_root() / "live" / "audit.jsonl"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries: list[AuditLogEntry] = []
    for line in reversed(lines[-500:]):
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        entries.append(
            AuditLogEntry(
                audit_id=str(raw.get("audit_id") or ""),
                ts=str(raw.get("ts") or ""),
                kind=str(raw.get("kind") or ""),
                outcome=str(raw.get("outcome") or ""),
                server=str(raw.get("server") or ""),
                intent=raw.get("intent_normalized"),
                error=raw.get("error"),
            )
        )
        if len(entries) >= limit:
            break
    return entries


def _recent_runs(limit: int = 8) -> list[RecentRun]:
    """Best-effort scan of run dirs (mirrors list_runs summary fields)."""
    import csv
    import sys

    host = sys.modules.get("api_server") or sys.modules.get("agent.api_server")
    runs_dir: Path | None = None
    if host is not None:
        runs_attr = getattr(host, "RUNS_DIR", None)
        if callable(runs_attr):
            try:
                runs_dir = Path(runs_attr())
            except Exception:
                runs_dir = None
        elif runs_attr is not None:
            runs_dir = Path(runs_attr)
    if runs_dir is None:
        runs_dir = Path(__file__).resolve().parents[2] / "runs"
    if not runs_dir.exists():
        return []

    run_dirs = sorted(
        [d for d in runs_dir.iterdir() if d.is_dir()],
        key=lambda x: x.name,
        reverse=True,
    )
    results: list[RecentRun] = []
    for d in run_dirs[:limit]:
        status_val = "unknown"
        state_file = d / "state.json"
        if state_file.exists():
            try:
                status_val = str(json.loads(state_file.read_text(encoding="utf-8")).get("status") or "unknown").lower()
            except (OSError, json.JSONDecodeError):
                pass
        elif (d / "artifacts" / "equity.csv").exists():
            status_val = "success"

        prompt = "Manual Analysis"
        req_file = d / "req.json"
        if req_file.exists():
            try:
                prompt = str(json.loads(req_file.read_text(encoding="utf-8")).get("prompt") or prompt)
            except (OSError, json.JSONDecodeError):
                pass

        total_return = None
        sharpe = None
        metrics_file = d / "artifacts" / "metrics.csv"
        if metrics_file.exists():
            try:
                with open(metrics_file, encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        total_return = float(row.get("total_return", 0) or 0)
                        sharpe = float(row.get("sharpe", 0) or 0)
                        break
            except (OSError, ValueError):
                pass

        created_at = d.name
        results.append(
            RecentRun(
                run_id=d.name,
                created_at=created_at,
                status=status_val,
                prompt=prompt[:160],
                total_return=total_return,
                sharpe=sharpe,
            )
        )
    return results


def _paper_wallet_summary() -> PaperWalletSummary:
    try:
        from src.trading.paper_wallet import snapshot

        snap = snapshot()
        return PaperWalletSummary(
            currency=str(snap.get("currency") or "INR"),
            cash=float(snap.get("cash") or 0),
            equity=float(snap.get("equity") or 0),
            total_deposited=float(snap.get("total_deposited") or 0),
            realized_pnl=float(snap.get("realized_pnl") or 0),
            unrealized_pnl=float(snap.get("unrealized_pnl") or 0),
            total_pnl=float(snap.get("total_pnl") or 0),
            total_pnl_pct=float(snap.get("total_pnl_pct") or 0),
            open_positions=int(snap.get("open_positions") or 0),
        )
    except Exception:
        return PaperWalletSummary()


def _orders_capability() -> OrdersCapability:
    upstox_path = get_runtime_root() / "upstox.json"
    upstox_configured = False
    if upstox_path.exists():
        try:
            data = json.loads(upstox_path.read_text(encoding="utf-8"))
            upstox_configured = bool(str(data.get("access_token") or "").strip())
        except (OSError, json.JSONDecodeError):
            upstox_configured = False
    return OrdersCapability(
        can_place_orders=True,
        paper_supported=True,
        live_supported=True,
        upstox_configured=upstox_configured,
        upstox_live_orders=False,
        note=(
            "Deposit paper cash via Settings or POST /paper/deposit, then place "
            "Upstox paper orders (agent trading_place_order). Fills debit the "
            "local wallet so you can see profit/loss before going live. "
            "Live actions append to ~/.vibe-trading/live/audit.jsonl."
        ),
    )


def build_dashboard_summary() -> DashboardSummary:
    now = datetime.now(timezone.utc)
    outcomes = _load_watcher_outcomes()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())
    month_start = today_start.replace(day=1)

    recent_trades: list[RecentTrade] = []
    for row in outcomes[:12]:
        instrument, side = _instrument_from_outcome(row)
        recent_trades.append(
            RecentTrade(
                signal_id=str(row.get("signal_id") or ""),
                closed_at=str(row.get("closed_at") or "") or None,
                pnl_pct=round(float(row.get("pnl_pct") or 0.0), 4),
                exit_reason=str(row.get("exit_reason") or ""),
                instrument=instrument,
                side=side,
            )
        )

    return DashboardSummary(
        generated_at=now.isoformat(),
        today=_period_from_outcomes(outcomes, start=today_start, label="Today"),
        week=_period_from_outcomes(outcomes, start=week_start, label="This week"),
        month=_period_from_outcomes(outcomes, start=month_start, label="This month"),
        all_time=_period_from_outcomes(outcomes, start=None, label="All time"),
        daily=_daily_series(outcomes, days=30),
        monthly=_monthly_series(outcomes, months=12),
        recent_trades=recent_trades,
        recent_audit=_recent_audit(25),
        recent_runs=_recent_runs(8),
        open_positions=_count_open_positions(),
        orders=_orders_capability(),
        paper_wallet=_paper_wallet_summary(),
        sources={
            "watcher_db": str(get_runtime_root() / "watcher" / "watcher.db"),
            "audit_log": str(get_runtime_root() / "live" / "audit.jsonl"),
            "upstox_config": str(get_runtime_root() / "upstox.json"),
            "paper_wallet": str(get_runtime_root() / "paper_wallet.json"),
        },
    )


def register_dashboard_routes(
    app: FastAPI,
    require_auth: AuthDep | None = None,
) -> None:
    if require_auth is None:
        import sys as _sys

        host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
        if host is None:  # pragma: no cover
            raise RuntimeError("register_dashboard_routes: api_server not loaded")
        require_auth = host.require_auth

    @app.get(
        "/dashboard/summary",
        response_model=DashboardSummary,
        dependencies=[Depends(require_auth)],
    )
    async def dashboard_summary() -> DashboardSummary:
        """Aggregated home-dashboard P&L, orders capability, and recent logs."""
        return build_dashboard_summary()
