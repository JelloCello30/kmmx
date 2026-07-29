import unittest
from decimal import Decimal

from kmmx.config import StrategyConfig
from kmmx.models import Incentive, OrderBook
from kmmx.strategy import AdaptiveRewardStrategy


def make_book():
    return OrderBook.from_api(
        "TEST",
        {
            "orderbook_fp": {
                "yes_dollars": [["0.40", "10"]],
                "no_dollars": [["0.55", "10"]],
            }
        },
    )


def make_incentive():
    return Incentive.from_api(
        {
            "id": "i",
            "market_ticker": "TEST",
            "start_date": "2026-01-01T00:00:00Z",
            "end_date": "2030-01-02T00:00:00Z",
            "period_reward": 10000000000,
            "target_size_fp": "100.00",
            "discount_factor_bps": 5000,
            "incentive_type": "liquidity",
        }
    )


class StrategyTests(unittest.TestCase):
    def test_no_incentive_disables_quotes_by_default(self):
        strategy = AdaptiveRewardStrategy(StrategyConfig(), 2)
        plan = strategy.make_plan(make_book(), Decimal("0.01"), Decimal("0"), None)
        self.assertFalse(plan.quotes)
        self.assertIn("no active liquidity incentive", plan.reasons)

    def test_long_inventory_reduces_bid_and_increases_ask_size(self):
        config = StrategyConfig(min_expected_value_per_contract=Decimal("-1"))
        strategy = AdaptiveRewardStrategy(config, 2)
        plan = strategy.make_plan(make_book(), Decimal("0.01"), Decimal("80"), make_incentive())
        self.assertIsNotNone(plan.bid)
        self.assertIsNotNone(plan.ask)
        self.assertLess(plan.bid.price, Decimal("0.40"))
        self.assertGreater(plan.ask.count, plan.bid.count)


if __name__ == "__main__":
    unittest.main()
