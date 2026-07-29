import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from kmmx.models import OrderBook, Quote, QuotePlan, Trade
from kmmx.paper import PaperBroker


class PaperBrokerTests(unittest.TestCase):
    def test_trade_must_clear_queue_before_fill(self):
        now = datetime.now(timezone.utc)
        book = OrderBook.from_api(
            "TEST",
            {
                "orderbook_fp": {
                    "yes_dollars": [["0.40", "5"]],
                    "no_dollars": [["0.55", "5"]],
                }
            },
            now,
        )
        quote = Quote(
            "bid", Decimal("0.40"), Decimal("2"), Decimal("0"), Decimal("0"), Decimal("0")
        )
        plan = QuotePlan("TEST", Decimal("0.425"), Decimal("0"), quote, None)
        broker = PaperBroker(Decimal("100"))
        broker.replace_quotes(plan, book, now)

        first = Trade("1", "TEST", Decimal("0.40"), Decimal("4"), "ask", now)
        broker.process_trades([first])
        self.assertEqual(broker.position, Decimal("0"))
        self.assertEqual(broker.orders["bid"].queue_ahead, Decimal("1"))

        second = Trade(
            "2", "TEST", Decimal("0.40"), Decimal("2"), "ask", now + timedelta(seconds=1)
        )
        broker.process_trades([second])
        self.assertEqual(broker.position, Decimal("1"))
        self.assertEqual(broker.cash, Decimal("99.60"))

    def test_expired_order_does_not_fill(self):
        now = datetime.now(timezone.utc)
        book = OrderBook.from_api(
            "TEST",
            {
                "orderbook_fp": {
                    "yes_dollars": [["0.40", "0"]],
                    "no_dollars": [["0.55", "5"]],
                }
            },
            now,
        )
        quote = Quote(
            "bid", Decimal("0.40"), Decimal("2"), Decimal("0"), Decimal("0"), Decimal("0")
        )
        plan = QuotePlan("TEST", Decimal("0.425"), Decimal("0"), quote, None)
        broker = PaperBroker(Decimal("100"), order_ttl_seconds=1)
        broker.replace_quotes(plan, book, now)
        trade = Trade(
            "late", "TEST", Decimal("0.40"), Decimal("10"), "ask", now + timedelta(seconds=2)
        )
        broker.process_trades([trade])
        self.assertEqual(broker.position, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
