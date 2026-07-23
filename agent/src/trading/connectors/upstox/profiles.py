"""Built-in Upstox connector profiles.

Upstox (https://upstox.com) exposes a free Developer API for Indian markets:
NSE/BSE equities, indices (Nifty 50 / Bank Nifty / FinNifty / midcap), stock &
index futures/options, MCX commodities (Gold / Silver / Crude Oil), and
currency futures (USDINR, EURINR, …).

Paper-only by design: Upstox has no sandbox and no runtime paper/live
discriminator — a single access token reads the same account. Following the
Dhan / Shoonya / Longbridge precedent, this connector ships read-only
paper/live profiles plus a locally simulated paper-trade profile, and exposes
NO live order placement.
"""

from __future__ import annotations

from src.trading.types import READ_CAPABILITIES, TradingProfile

UPSTOX_PROFILES: tuple[TradingProfile, ...] = (
    TradingProfile(
        id="upstox-paper-sdk",
        connector="upstox",
        label="Upstox Paper · REST (India)",
        environment="paper",
        transport="broker_sdk",
        capabilities=READ_CAPABILITIES,
        readonly=True,
        config={"profile": "paper"},
        notes=(
            "Reads Indian market data via Upstox Developer API: NSE/BSE cash, "
            "Nifty 50 / Bank Nifty / FinNifty / midcap indices, stock & index "
            "F&O, MCX Gold/Silver/Crude, and currency futures (USDINR…). "
            "Configure access_token in ~/.vibe-trading/upstox.json."
        ),
    ),
    TradingProfile(
        id="upstox-paper-trade",
        connector="upstox",
        label="Upstox Paper · REST Trade (India)",
        environment="paper",
        transport="broker_sdk",
        capabilities=READ_CAPABILITIES + ("orders.place",),
        readonly=False,
        config={"profile": "paper"},
        notes=(
            "Places PAPER orders simulated locally using real Upstox market "
            "data — no real money at risk. Live order placement is not "
            "supported (no sandbox / paper-live discriminator)."
        ),
    ),
    TradingProfile(
        id="upstox-live-sdk-readonly",
        connector="upstox",
        label="Upstox Live · REST Read-Only (India)",
        environment="live",
        transport="broker_sdk",
        capabilities=READ_CAPABILITIES,
        readonly=True,
        config={"profile": "live-readonly"},
        notes=(
            "Reads a live Upstox account (profile, funds, positions, orders, "
            "quotes, history). Order placement is not exposed in this profile."
        ),
    ),
)
