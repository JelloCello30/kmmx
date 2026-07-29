import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from kmmx.state import DailyEquityStore


class DailyEquityStoreTests(unittest.TestCase):
    def test_restart_does_not_reset_same_day_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "state.json")
            store = DailyEquityStore(path)
            now = datetime(2026, 7, 16, 10, tzinfo=timezone.utc)
            self.assertEqual(store.get_or_create(Decimal("100"), now), Decimal("100"))
            self.assertEqual(store.get_or_create(Decimal("80"), now), Decimal("100"))
            next_day = now + timedelta(days=1)
            self.assertEqual(store.get_or_create(Decimal("85"), next_day), Decimal("85"))


if __name__ == "__main__":
    unittest.main()
