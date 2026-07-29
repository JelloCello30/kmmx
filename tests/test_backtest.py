import json
import tempfile
import unittest
from pathlib import Path

from kmmx.backtest import run_backtest
from kmmx.config import BotConfig


class BacktestTests(unittest.TestCase):
    def test_replay_uses_trade_driven_fill(self):
        market = {
            "ticker": "TEST",
            "title": "Test market",
            "status": "active",
            "close_time": "2030-01-01T00:00:00Z",
            "volume_24h_fp": "100.00",
            "price_ranges": [{"start": "0", "end": "1", "step": "0.01"}],
        }
        incentive = {
            "id": "i",
            "market_ticker": "TEST",
            "start_date": "2026-01-01T00:00:00Z",
            "end_date": "2030-01-01T00:00:00Z",
            "period_reward": 10000000000,
            "target_size_fp": "100.00",
            "discount_factor_bps": 5000,
            "incentive_type": "liquidity",
        }
        book = {
            "orderbook_fp": {
                "yes_dollars": [["0.40", "5"]],
                "no_dollars": [["0.55", "5"]],
            }
        }
        rows = [
            {
                "type": "metadata",
                "format_version": 1,
                "ticker": "TEST",
                "market": market,
                "incentive": incentive,
            },
            {
                "type": "snapshot",
                "observed_at": "2026-07-16T00:00:00Z",
                "ticker": "TEST",
                "orderbook": book,
                "trades": [],
            },
            {
                "type": "snapshot",
                "observed_at": "2026-07-16T00:00:02Z",
                "ticker": "TEST",
                "orderbook": book,
                "trades": [
                    {
                        "trade_id": "t1",
                        "ticker": "TEST",
                        "yes_price_dollars": "0.40",
                        "count_fp": "20.00",
                        "taker_book_side": "ask",
                        "created_time": "2026-07-16T00:00:01Z",
                        "is_block_trade": False,
                    }
                ],
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recording.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            result = run_backtest(str(path), BotConfig())
        self.assertGreaterEqual(result.fills, 1)
        self.assertNotEqual(str(result.ending_position), "0")


if __name__ == "__main__":
    unittest.main()
