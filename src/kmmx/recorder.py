from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Set

from .api import KalshiClient
from .models import parse_time


def _matching_incentive(client: KalshiClient, ticker: str) -> Optional[dict]:
    for item in client.get_incentives(status="active", incentive_type="liquidity", limit=10000):
        if item.get("market_ticker") == ticker:
            return item
    return None


def collect_market_data(
    client: KalshiClient,
    ticker: str,
    output_path: str,
    interval_seconds: float,
    duration_seconds: float,
) -> int:
    market = client.get_market(ticker)
    incentive = _matching_incentive(client, ticker)
    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    last_observed = datetime.now(timezone.utc)
    seen_trade_ids: Set[str] = set()
    snapshots = 0

    with output.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "metadata",
                    "format_version": 1,
                    "recorded_at": last_observed.isoformat(),
                    "ticker": ticker,
                    "market": market,
                    "incentive": incentive,
                },
                separators=(",", ":"),
            )
            + "\n"
        )
        handle.flush()
        while duration_seconds <= 0 or time.monotonic() - start < duration_seconds:
            observed = datetime.now(timezone.utc)
            book = client.get_orderbook(ticker)
            trade_rows = client.get_trades(
                ticker, min_ts=int(last_observed.timestamp()), limit=1000
            )
            new_trades = []
            for trade in trade_rows:
                trade_id = str(trade.get("trade_id", ""))
                created = parse_time(trade.get("created_time"))
                if (
                    trade_id
                    and trade_id not in seen_trade_ids
                    and created is not None
                    and created > last_observed
                ):
                    seen_trade_ids.add(trade_id)
                    new_trades.append(trade)
            handle.write(
                json.dumps(
                    {
                        "type": "snapshot",
                        "observed_at": observed.isoformat(),
                        "ticker": ticker,
                        "orderbook": book,
                        "trades": sorted(new_trades, key=lambda row: row.get("created_time", "")),
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )
            handle.flush()
            snapshots += 1
            last_observed = observed
            remaining = interval_seconds - (datetime.now(timezone.utc) - observed).total_seconds()
            if remaining > 0:
                time.sleep(remaining)
    return snapshots
