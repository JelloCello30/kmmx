from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal
from typing import Optional, Sequence

from .api import KalshiClient, KalshiSigner
from .backtest import run_backtest
from .config import BotConfig, load_config
from .engine import find_incentive, run_live, run_paper
from .models import Market, OrderBook, decimal
from .recorder import collect_market_data
from .scanner import scan_markets
from .strategy import AdaptiveRewardStrategy


LIVE_PHRASE = "I_UNDERSTAND_REAL_MONEY_IS_AT_RISK"


def _client(config: BotConfig, authenticated: bool = False) -> KalshiClient:
    signer = KalshiSigner.from_environment() if authenticated else None
    return KalshiClient(
        config.api.base_url,
        timeout_seconds=config.api.timeout_seconds,
        max_retries=config.api.max_retries,
        signer=signer,
    )


def _money(value: Decimal) -> str:
    return "$%.4f" % value


def _add_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="JSON config path (defaults are used when omitted)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kmmx", description="Incentive-aware Kalshi market maker")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="rank active liquidity incentives")
    scan.add_argument("--limit", type=int, default=20)
    scan.add_argument("--json", action="store_true", help="print machine-readable JSON")
    _add_config(scan)

    quote = subparsers.add_parser("quote", help="show a quote decision without placing orders")
    quote.add_argument("ticker")
    quote.add_argument("--position", default="0", help="signed YES inventory")
    _add_config(quote)

    collect = subparsers.add_parser("collect", help="record books and public trades to JSONL")
    collect.add_argument("ticker")
    collect.add_argument("--output", required=True)
    collect.add_argument("--duration", type=float, default=3600)
    collect.add_argument("--interval", type=float)
    _add_config(collect)

    backtest = subparsers.add_parser("backtest", help="replay a JSONL recording")
    backtest.add_argument("path")
    _add_config(backtest)

    run = subparsers.add_parser("run", help="run the continuous quote loop")
    run.add_argument("ticker")
    modes = run.add_mutually_exclusive_group()
    modes.add_argument("--paper", action="store_true", help="simulate; this is the default")
    modes.add_argument("--live", action="store_true", help="place real/demo exchange orders")
    run.add_argument("--duration", type=float, default=0, help="seconds; 0 runs until stopped")
    _add_config(run)
    return parser


def _scan(args: argparse.Namespace, config: BotConfig) -> int:
    candidates = scan_markets(_client(config), config, max(1, args.limit))
    if args.json:
        print(json.dumps([item.as_dict() for item in candidates], indent=2))
        return 0
    headers = ("rank", "ticker", "pool/day", "model/day", "spread", "24h vol", "flags")
    print("%-4s %-37s %10s %10s %8s %10s %s" % headers)
    for rank, item in enumerate(candidates, 1):
        print(
            "%-4d %-37s %10s %10s %8s %10s %s"
            % (
                rank,
                item.ticker[:37],
                _money(item.reward_per_day),
                _money(item.estimated_net_per_day),
                str(item.spread),
                str(item.volume_24h),
                ", ".join(item.warnings) or "-",
            )
        )
    print("model/day is an estimate, not expected or guaranteed profit")
    return 0


def _quote(args: argparse.Namespace, config: BotConfig) -> int:
    client = _client(config)
    market = Market.from_api(client.get_market(args.ticker))
    book = OrderBook.from_api(args.ticker, client.get_orderbook(args.ticker))
    incentive = find_incentive(client, args.ticker)
    tick = market.tick_at(book.midpoint)
    plan = AdaptiveRewardStrategy(config.strategy, config.runtime.poll_interval_seconds).make_plan(
        book, tick, decimal(args.position), incentive
    )
    print(
        json.dumps(
            {
                "ticker": args.ticker,
                "title": market.title,
                "close_time": market.close_time.isoformat() if market.close_time else None,
                "rules_primary": market.raw.get("rules_primary", ""),
                "rules_secondary": market.raw.get("rules_secondary", ""),
                "book": {
                    "bid": str(book.best_bid),
                    "ask": str(book.best_ask),
                    "midpoint": str(book.midpoint),
                    "microprice": str(book.microprice),
                    "imbalance": str(book.imbalance),
                },
                "active_incentive": (
                    {
                        "reward_dollars": str(incentive.reward_dollars),
                        "reward_per_day": str(incentive.reward_per_day),
                        "target_size": str(incentive.target_size),
                        "discount_factor": str(incentive.discount_factor),
                        "end": incentive.end.isoformat(),
                    }
                    if incentive
                    else None
                ),
                "fair_value": str(plan.fair_value),
                "volatility": str(plan.volatility),
                "quotes": [
                    {
                        "side": item.side,
                        "price": str(item.price),
                        "count": str(item.count),
                        "expected_value_per_contract_cycle": str(item.expected_value_per_contract),
                        "modeled_reward_component": str(item.reward_value_per_contract),
                        "modeled_trading_component": str(item.trading_value_per_contract),
                    }
                    for item in plan.quotes
                ],
                "reasons": list(plan.reasons),
            },
            indent=2,
        )
    )
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "scan":
            return _scan(args, config)
        if args.command == "quote":
            return _quote(args, config)
        if args.command == "collect":
            interval = args.interval or config.runtime.poll_interval_seconds
            count = collect_market_data(
                _client(config), args.ticker, args.output, interval, args.duration
            )
            print("recorded %s snapshots to %s" % (count, args.output))
            return 0
        if args.command == "backtest":
            print(json.dumps(run_backtest(args.path, config).as_dict(), indent=2))
            return 0
        if args.command == "run":
            if args.live:
                if os.environ.get("KMMX_LIVE_CONFIRM") != LIVE_PHRASE:
                    raise RuntimeError(
                        "Live mode blocked: set KMMX_LIVE_CONFIRM='%s'" % LIVE_PHRASE
                    )
                if args.ticker not in config.runtime.approved_live_tickers:
                    raise RuntimeError(
                        "Live mode blocked: add %s to runtime.approved_live_tickers in the config"
                        % args.ticker
                    )
                run_live(_client(config, authenticated=True), config, args.ticker, args.duration)
            else:
                run_paper(_client(config), config, args.ticker, args.duration)
            return 0
        parser.error("unknown command")
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
        return 130
    except Exception as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
