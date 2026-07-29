import unittest
from decimal import Decimal

from kmmx.config import RiskConfig
from kmmx.models import OrderBook, Quote, QuotePlan
from kmmx.risk import RiskEngine


class RiskTests(unittest.TestCase):
    def test_capital_limit_resizes_both_quotes(self):
        book = OrderBook.from_api(
            "TEST",
            {
                "orderbook_fp": {
                    "yes_dollars": [["0.40", "10"]],
                    "no_dollars": [["0.55", "10"]],
                }
            },
        )
        bid = Quote("bid", Decimal("0.40"), Decimal("10"), Decimal("0"), Decimal("0"), Decimal("0"))
        ask = Quote("ask", Decimal("0.45"), Decimal("10"), Decimal("0"), Decimal("0"), Decimal("0"))
        plan = QuotePlan("TEST", Decimal("0.425"), Decimal("0"), bid, ask)
        risk = RiskEngine(RiskConfig(max_market_capital=Decimal("1")))
        resized = risk.sanitize(plan, book, Decimal("0"), Decimal("100"), Decimal("0"))
        self.assertLess(resized.collateral, Decimal("1.01"))
        self.assertIn("quote sizes reduced by capital limits", resized.reasons)


if __name__ == "__main__":
    unittest.main()
