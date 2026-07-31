# KMMX

KMMX is a risk-first, incentive-aware market-making bot for Kalshi event contracts. It recreates
the useful idea behind the referenced KMM project—earning liquidity subsidies while limiting
trading losses—and adds formal quote economics, inventory/volatility controls, a public-data
recorder, queue-aware replay, and hard live-trading gates.

It does **not** promise profit. Start with the demo exchange or paper mode. Liquidity rewards are
competitive, changeable, eligibility-limited, and may not cover adverse selection.

## Quick start

Python 3.9+ is supported and paper tools use only the standard library.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
cp config.example.json config.json

# Rank currently incentivized markets (public API, no key needed)
kmmx scan --limit 20 --config config.json

# Inspect one quote decision without placing an order
kmmx quote MARKET-TICKER --config config.json

# Record books and trades for a replayable test
kmmx collect MARKET-TICKER --output data/market.jsonl --duration 3600 --config config.json

# Replay recorded data
kmmx backtest data/market.jsonl --config config.json

# Run against live public data with simulated fills and cash
kmmx run MARKET-TICKER --paper --duration 3600 --config config.json
```

Run tests with the standard library:

```bash
python -m unittest discover -v
```

## Live mode

Install the cryptographic signing dependency:

```bash
python -m pip install '.[live]'
```

Set credentials without putting secrets in the repository:

```bash
export KALSHI_API_KEY_ID='your-key-id'
export KALSHI_PRIVATE_KEY_PATH='/absolute/path/to/private-key.pem'
```

Explicitly add each reviewed market ticker to `runtime.approved_live_tickers` in `config.json`.
This whitelist is the capital-approval boundary; scanning a market never approves it.

Use a **demo** key with `"environment": "demo"` first. Production live mode requires the command
flag and this exact second confirmation:

```bash
export KMMX_LIVE_CONFIRM='I_UNDERSTAND_REAL_MONEY_IS_AT_RISK'
kmmx run MARKET-TICKER --live --config config.json
```

The bot creates short-lived, post-only quotes. On a clean shutdown it cancels its own orders and
deletes its order group. A kill-switch file (default `.kmmx-stop`) causes the loop to cancel and
exit. Network loss can delay cancellation, which is why every submitted order also has a short
exchange-side expiration. Live account equity is stored once per UTC day in `.kmmx-state.json`, so
restarting the process does not reset the daily loss baseline.

## How quoting works

For each cycle, KMMX:

1. parses the reciprocal YES/NO book and computes midpoint, microprice, imbalance, and spread;
2. updates an EWMA fair value and short-horizon volatility;
3. skews price and size away from current inventory;
4. estimates fill loss plus conservative incentive accrual;
5. removes negative-EV sides unless explicitly configured to quote without an incentive;
6. applies hard risk limits; and
7. reconciles only bot-owned orders.

The incentive estimate divides the pool by its duration, target size, and a configurable
`reward_competition_multiple`. This is a planning approximation because the API does not reveal
future aggregate participant scores. Backtest output keeps estimated rewards separate from trading
P&L so the assumption is visible. Set `maker_fee_estimate_per_contract` conservatively for any
market listed in Kalshi's maker-fee schedule; the zero default is not valid for every market.

See [RESEARCH.md](RESEARCH.md) for the research summary and source links.
