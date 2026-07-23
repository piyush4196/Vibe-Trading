# Vibe-Trading

Your personal AI trading agent — research, backtest, paper-trade, and (where brokers allow) place bounded live orders.

## How this helps you trade

| What you want | What Vibe-Trading does |
|---------------|------------------------|
| Research a stock / options idea | Chat agent pulls market data, news, factors, and writes a thesis |
| Test a strategy before risking money | Backtests (including India NSE/BSE) + Shadow Account |
| Watch a symbol all day | Always-on **watcher** (Upstox) scores multi-timeframe setups |
| Only act on strong setups | Default gate: **confidence ≥ 80%** and risk/reward ≥ 1:2 |
| Place 1 share / 1 lot automatically | Opt-in **auto-trade** on the watcher (paper for India brokers) |
| Know when to exit | Position monitor tracks stop / targets / early-exit alerts (and can auto-close) |

**Important for India (Reliance, NSE F&O):** Upstox / Dhan / Shoonya connectors are **paper + read-only** for live accounts. Auto-trade places **simulated paper orders** with real market data — no real money unless you use a broker that supports mandate-gated live trading (e.g. Alpaca, Tiger, Futu, Binance, OKX).

---

## Quick start

```bash
pip install vibe-trading-ai
# or from this repo:
# pip install -e .

vibe-trading init          # LLM key + env
vibe-trading               # interactive agent
vibe-trading serve         # Web UI (default http://localhost:8899)
```

Configure Upstox for Indian market data / paper trading in `~/.vibe-trading/upstox.json` (access token from the Upstox developer portal).

---

## Buy 1 qty of Reliance when confidence ≥ 80%

### Steps

1. **Install + init**
   ```bash
   vibe-trading init
   ```

2. **Select the India paper trading profile**
   ```bash
   vibe-trading connector use upstox-paper-trade
   ```

3. **Fund the local paper wallet** (Web UI → Settings / Paper, or ask the agent to deposit).

4. **Point the watcher at Reliance only, enable auto-trade of 1 qty, confidence 80%**
   ```bash
   vibe-trading watch config --symbols RELIANCE --enable-auto-trade --quantity 1 --min-confidence 80 --profile upstox-paper-trade
   ```

5. **Start the watcher** (market hours)
   ```bash
   vibe-trading watch start --symbols RELIANCE --auto-trade --quantity 1 --min-confidence 80
   ```

6. **Check status**
   ```bash
   vibe-trading watch status
   ```

When a multi-timeframe setup clears **confidence ≥ 80%** and the RR gate, the watcher:

- sends a Telegram alert (if configured), and  
- places a **paper market buy/sell of 1 qty** of `RELIANCE`, then  
- monitors stop / targets and can **auto-exit** the same quantity.

Optional Telegram:
```bash
vibe-trading watch config --set-telegram-token <BOT_TOKEN> --set-telegram-chat-id <CHAT_ID>
```

### One-shot paper buy via the chat agent (no watcher)

```bash
vibe-trading connector use upstox-paper-trade
vibe-trading run -p "Place a paper market buy of 1 quantity of RELIANCE using the selected connector"
```

---

## Options: monitor entry / exit, trade only at 80%+ confidence

### Steps

1. **Watch Reliance cash + nearest ATM CE/PE**
   ```bash
   vibe-trading watch config --symbols RELIANCE --include-stock-options --enable-auto-trade --quantity 1 --min-confidence 80
   vibe-trading watch start --symbols RELIANCE --auto-trade --quantity 1 --min-confidence 80
   ```
   With `--include-stock-options` (or config flag), the universe adds **nearest-expiry ATM call and put** for that underlying. The same confidence / RR gates apply; exits use stop, targets, and early-exit rules.

2. **Index options (Nifty / BankNifty ATM)**
   ```bash
   vibe-trading watch config --include-index-options --enable-auto-trade --min-confidence 80
   ```

3. **Ask the chat agent to keep watching (scheduled research)**  
   Enable the scheduler (`VIBE_TRADING_ENABLE_SCHEDULER=1`), then create a scheduled run or Research Goal with a prompt like:

   > Monitor RELIANCE options. Every cycle: fetch chain / price action, score entry and exit. Only recommend or place a paper order if confidence ≥ 80%. Tell me clearly: ENTRY now / WAIT / EXIT now.

4. **Manual agent prompt (session)**
   ```text
   Keep monitoring RELIANCE. For cash and ATM options:
   - ENTRY when multi-TF alignment confidence ≥ 80% and RR ≥ 1:2
   - EXIT on stop, target, or early reversal
   If confidence ≥ 80%, place paper order qty 1 via trading_place_order on upstox-paper-trade.
   Otherwise only alert me — do not order.
   ```

---

## What was implemented for confidence-gated orders

Previously the watcher only **alerted** at ≥80% confidence. It now supports opt-in **auto-trade**:

| Setting | Default | Meaning |
|---------|---------|---------|
| `auto_trade_enabled` | `false` | Must turn on to place orders |
| `min_confidence` | `80` | Signal must clear this score |
| `auto_trade_quantity` | `1` | Order size |
| `auto_trade_profile_id` | `upstox-paper-trade` | Connector profile |
| `auto_trade_symbols` | `[]` | Empty = any watched symbol; else allow-list |
| `auto_trade_on_exit` | `true` | Flatten on stop/target |
| `watch_only_symbols` | `[]` | Narrow universe (e.g. only RELIANCE) |
| `include_stock_options` / `include_index_options` | `false` | Add ATM options to the watchlist |

Config file: `~/.vibe-trading/watcher/config.json`

---

## Useful commands

```bash
vibe-trading watch config --print
vibe-trading watch once --symbols RELIANCE   # dry single scan
vibe-trading watch start --auto-trade --quantity 1 --symbols RELIANCE
vibe-trading watch status
vibe-trading watch stop

vibe-trading connector list
vibe-trading connector use upstox-paper-trade
vibe-trading run -p "Your research or order instruction"
```

---

## Safety

- Auto-trade is **off by default**.
- India live order placement is **blocked by design** on Upstox/Dhan/Shoonya (no paper/live API split).
- Live brokers that support trading require a **user-committed mandate**, kill switch, and audit trail.
- This is experimental software — not financial advice. You are responsible for orders and risk.

## License

MIT — see [LICENSE](LICENSE).
Get-NetTCPConnection -LocalPort 8899 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }; vibe-trading serve --port 8899