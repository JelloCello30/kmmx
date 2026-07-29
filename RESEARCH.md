# KMM research and design notes

Research date: 2026-07-16.

## What the referenced project actually does

Trevor McKinney's [Market Making on Kalshi](https://trevormckinney.com/work/kmm)
is a Python system with research, strategy, orchestration, and per-market execution layers.
It uses an AI operator for market research and parameter selection, with human approval before
deploying new capital. Its primary thesis is not that naive market making reliably captures the
spread. The author says the trading component lost money and that Kalshi liquidity rewards more
than covered adverse-selection losses. The reported result is +$1,081.93 on $650 of seed over 16
weeks, but the page does not publish code, fills, drawdown, capital-time weighting, or an auditable
backtest. Treat the result as an anecdote rather than independently verified performance.

## Current exchange mechanics

Kalshi's [Liquidity Incentive Program](https://help.kalshi.com/en/articles/13823851-liquidity-incentive-program)
scores resting orders using periodic snapshots, size, and distance from the reference price. A
participant receives a pro-rata share of a market's reward pool. The program can be modified or
ended at Kalshi's discretion, and eligibility is restricted. Therefore, an incentive is a variable
subsidy, not guaranteed yield.

The implementation follows the current official API:

- [Production/demo endpoints](https://docs.kalshi.com/getting_started/api_environments)
- [RSA-PSS API authentication](https://docs.kalshi.com/getting_started/api_keys)
- [Fixed-point order-book format](https://docs.kalshi.com/getting_started/orderbook_responses)
- [V2 post-only order creation](https://docs.kalshi.com/api-reference/orders/create-order-v2)
- [Active incentive endpoint](https://docs.kalshi.com/api-reference/incentive-programs/get-incentives)
- [Public trade feed](https://docs.kalshi.com/api-reference/market/get-trades)
- [API rate limits](https://docs.kalshi.com/getting_started/rate_limits)

Kalshi exposes YES bids and NO bids. A YES ask is inferred as `1 - best NO bid`. The V2 order API
uses a single YES-denominated book: `bid` buys YES and `ask` sells YES (economically equivalent to
buying NO at `1 - price`).

## Similar approaches considered

The quoting model borrows the useful part of inventory-aware market making associated with
[Avellaneda-Stoikov](https://doi.org/10.1080/14697680701381228): move the reservation price away
from accumulated inventory and widen under volatility. It does not pretend that the continuous,
diffusion-based model is a literal fit for discontinuous binary-event probabilities.

Prediction markets add risks that ordinary spread examples understate:

- jump risk when event information arrives;
- ambiguous or mismatched settlement rules;
- thin books whose midpoint is not a reliable fair probability;
- queue-position uncertainty and fills concentrated when a quote is stale;
- incentive dilution by competing liquidity;
- program and fee changes.

## Improvements implemented here

KMMX adds controls that are not demonstrated on the reference page:

1. Reward-aware expected-value gating. The bot estimates a deliberately discounted reward rate and
   quotes only when modeled reward accrual plus fill economics is non-negative. Reward estimates
   are reported separately from realized trading P&L.
2. Adaptive fair value. Top-of-book microprice, an EWMA, order-book imbalance, recent volatility,
   and inventory all influence price and size.
3. Conservative execution. Orders are post-only, expire quickly, use self-trade prevention, cancel
   on exchange pause, and can be attached to an exchange-side 15-second fill-limit group.
4. Bounded autonomy. Per-market/portfolio capital caps, position caps, daily loss, stale book,
   probability-jump, close-time, API-error, and file-based kill switches are enforced in code.
5. Safer ownership. The bot only cancels orders with its own `kmmx-` client-order prefix.
6. Auditable paper testing. A recorder captures order books plus public trades. The replay engine
   uses price-time queue estimates instead of assuming every touched quote fills.
7. Live trading requires three deliberate gates: `--live`, configured credentials, and an exact
   environment confirmation phrase. Paper mode is the default.

These are engineering improvements, not a claim of higher returns. No strategy can be shown to
"work better" without representative data, out-of-sample testing, and live slippage/fill evidence.

