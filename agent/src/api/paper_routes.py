"""Paper wallet HTTP routes — deposit play money, view cash/equity/P&L.

Mounted by ``agent/api_server.py`` via ``register_paper_routes(app, ...)``.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field

AuthDep = Callable[..., Awaitable[Any] | Any]


class DepositRequest(BaseModel):
    amount: float = Field(..., gt=0, description="Amount to add to paper cash")
    note: str = Field(default="", max_length=200)
    currency: Optional[str] = Field(default=None, max_length=8)


class WithdrawRequest(BaseModel):
    amount: float = Field(..., gt=0)
    note: str = Field(default="", max_length=200)


class PaperOrderRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=64)
    side: str = Field(..., description="buy or sell")
    quantity: float = Field(..., gt=0)
    price: Optional[float] = Field(default=None, gt=0, description="Optional limit/fill price")
    order_type: str = Field(default="market")


def register_paper_routes(
    app: FastAPI,
    require_auth: AuthDep | None = None,
) -> None:
    if require_auth is None:
        import sys as _sys

        host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
        if host is None:  # pragma: no cover
            raise RuntimeError("register_paper_routes: api_server not loaded")
        require_auth = host.require_auth

    from src.trading.paper_wallet import (
        PaperWalletError,
        apply_fill,
        deposit,
        reset_wallet,
        snapshot,
        withdraw,
    )

    @app.get("/paper/wallet", dependencies=[Depends(require_auth)])
    async def get_paper_wallet() -> dict[str, Any]:
        """Return paper cash, equity, positions, and recent ledger."""
        return snapshot()

    @app.post("/paper/deposit", dependencies=[Depends(require_auth)])
    async def paper_deposit(body: DepositRequest) -> dict[str, Any]:
        """Add virtual money to the paper trading wallet."""
        try:
            return deposit(body.amount, note=body.note, currency=body.currency)
        except PaperWalletError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/paper/withdraw", dependencies=[Depends(require_auth)])
    async def paper_withdraw(body: WithdrawRequest) -> dict[str, Any]:
        """Remove unused cash from the paper wallet."""
        try:
            return withdraw(body.amount, note=body.note)
        except PaperWalletError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/paper/reset", dependencies=[Depends(require_auth)])
    async def paper_reset() -> dict[str, Any]:
        """Wipe paper cash, positions, and realized PnL (keeps currency)."""
        return reset_wallet()

    @app.post("/paper/order", dependencies=[Depends(require_auth)])
    async def paper_order(body: PaperOrderRequest) -> dict[str, Any]:
        """Place a simple paper order against the local wallet (manual what-if).

        Prefer Upstox paper profile via the agent for market LTP fills; this
        endpoint lets you manually book a fill at an explicit price when quotes
        are unavailable.
        """
        side = body.side.strip().lower()
        if side not in ("buy", "sell"):
            raise HTTPException(status_code=400, detail="side must be buy or sell")
        price = float(body.price or 0)
        if price <= 0:
            raise HTTPException(
                status_code=400,
                detail="price is required for /paper/order (use agent Upstox paper for LTP market fills)",
            )
        try:
            return apply_fill(
                symbol=body.symbol.strip().upper(),
                side=side,
                quantity=float(body.quantity),
                price=price,
                order_id=f"MANUAL-{body.symbol}-{side}",
                broker="manual-paper",
            )
        except PaperWalletError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
