import unittest
from datetime import timezone
from decimal import Decimal

from kmmx.models import Incentive, OrderBook, parse_time


class OrderBookTests(unittest.TestCase):
    def test_reciprocal_book_metrics(self):
        book = OrderBook.from_api(
            "TEST",
            {
                "orderbook_fp": {
                    "yes_dollars": [["0.35", "4"], ["0.40", "10"]],
                    "no_dollars": [["0.30", "2"], ["0.55", "30"]],
                }
            },
        )
        self.assertEqual(book.best_bid, Decimal("0.40"))
        self.assertEqual(book.best_ask, Decimal("0.45"))
        self.assertEqual(book.spread, Decimal("0.05"))
        self.assertEqual(book.best_ask_size, Decimal("30"))
        self.assertEqual(book.size_at("ask", Decimal("0.45")), Decimal("30"))
        self.assertTrue(book.valid)

    def test_variable_length_fractional_timestamp(self):
        parsed = parse_time("2026-07-16T07:02:20.02509Z")
        self.assertEqual(parsed.microsecond, 25090)
        self.assertEqual(parsed.tzinfo, timezone.utc)

    def test_incentive_converts_centicents(self):
        incentive = Incentive.from_api(
            {
                "id": "i",
                "market_ticker": "TEST",
                "start_date": "2026-01-01T00:00:00Z",
                "end_date": "2026-01-02T00:00:00Z",
                "period_reward": 1000000,
                "target_size_fp": "1000.00",
                "discount_factor_bps": 5000,
                "incentive_type": "liquidity",
            }
        )
        self.assertEqual(incentive.reward_dollars, Decimal("100"))
        self.assertEqual(incentive.reward_per_day, Decimal("100"))
        self.assertEqual(incentive.score_multiplier(2), Decimal("0.25"))


if __name__ == "__main__":
    unittest.main()
