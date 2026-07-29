from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import IO
from typing import Any, Dict, List, Optional, Sequence

from .api import KalshiClient
from .models import QuotePlan, decimal


ORDER_PREFIX = "kmmx-"


@dataclass(frozen=True)
class LiveState:
    position: Decimal
    available_cash: Decimal
    equity: Decimal
    portfolio_capital: Decimal
    own_orders: Sequence[Dict[str, Any]]


def _book_side(order: Dict[str, Any]) -> str:
    side = order.get("book_side")
    if side in {"bid", "ask"}:
        return str(side)
    if order.get("action") == "buy" and order.get("side") == "yes":
        return "bid"
    return "ask"


def _order_price(order: Dict[str, Any]) -> Decimal:
    return decimal(order.get("yes_price_dollars", order.get("yes_price", 0)))


def _remaining(order: Dict[str, Any]) -> Decimal:
    return decimal(order.get("remaining_count_fp", order.get("remaining_count", 0)))


class LiveBroker:
    def __init__(
        self,
        client: KalshiClient,
        ticker: str,
        order_ttl_seconds: int,
        max_fills_per_15_seconds: int,
        process_lock_path: str,
    ) -> None:
        self.client = client
        self.ticker = ticker
        self.order_ttl_seconds = order_ttl_seconds
        self.run_id = uuid.uuid4().hex[:6]
        self._lock_handle: Optional[IO[str]] = None
        self._acquire_lock(process_lock_path)
        try:
            self.order_group_id = client.create_order_group(max_fills_per_15_seconds)
        except Exception:
            self._release_lock()
            raise

    def _acquire_lock(self, path: str) -> None:
        try:
            import fcntl
        except ImportError as exc:
            raise RuntimeError("live process locking is not supported on this platform") from exc
        lock_path = Path(path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise RuntimeError("another live KMMX process already owns this market") from exc
        self._lock_handle = handle

    def _release_lock(self) -> None:
        if self._lock_handle is None:
            return
        import fcntl

        fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
        self._lock_handle.close()
        self._lock_handle = None

    def is_owned(self, order: Dict[str, Any]) -> bool:
        prefix = "%s%s-" % (ORDER_PREFIX, self.run_id)
        return str(order.get("client_order_id", "")).startswith(prefix)

    def snapshot(self) -> LiveState:
        balance = self.client.get_balance()
        positions = self.client.get_positions()
        position = Decimal("0")
        portfolio_capital = Decimal("0")
        for item in positions:
            exposure = abs(decimal(item.get("market_exposure_dollars")))
            portfolio_capital += exposure
            if item.get("ticker") == self.ticker:
                position = decimal(item.get("position_fp", item.get("position", 0)))
        available = decimal(balance.get("balance_dollars"))
        portfolio_value = Decimal(str(balance.get("portfolio_value", 0))) / Decimal("100")
        equity = available + portfolio_value
        resting_orders = self.client.get_orders(status="resting")
        for order in resting_orders:
            if order.get("ticker") == self.ticker and self.is_owned(order):
                continue
            remaining = _remaining(order)
            price = _order_price(order)
            unit_collateral = price if _book_side(order) == "bid" else Decimal("1") - price
            portfolio_capital += max(Decimal("0"), remaining * unit_collateral)
        own_orders = [
            order
            for order in resting_orders
            if order.get("ticker") == self.ticker and self.is_owned(order)
        ]
        return LiveState(position, available, equity, portfolio_capital, own_orders)

    def reconcile(self, plan: QuotePlan, state: LiveState) -> None:
        desired = {quote.side: quote for quote in plan.quotes}
        keep_sides = set()
        cancel_ids: List[str] = []
        for order in state.own_orders:
            side = _book_side(order)
            quote = desired.get(side)
            matches = (
                quote is not None
                and side not in keep_sides
                and _order_price(order) == quote.price
                and _remaining(order) == quote.count
            )
            if matches:
                keep_sides.add(side)
            else:
                cancel_ids.append(str(order["order_id"]))
        if cancel_ids:
            self.client.batch_cancel_orders(cancel_ids)

        create_rows = []
        expiration = int(time.time()) + self.order_ttl_seconds
        for side, quote in desired.items():
            if side in keep_sides:
                continue
            create_rows.append(
                {
                    "ticker": self.ticker,
                    "client_order_id": "%s%s-%s-%s"
                    % (
                        ORDER_PREFIX,
                        self.run_id,
                        side,
                        uuid.uuid4().hex[:8],
                    ),
                    "side": side,
                    "count": str(quote.count),
                    "price": str(quote.price),
                    "time_in_force": "good_till_canceled",
                    "expiration_time": expiration,
                    "post_only": True,
                    "self_trade_prevention_type": "taker_at_cross",
                    "cancel_order_on_pause": True,
                    "order_group_id": self.order_group_id,
                    "subaccount": 0,
                    "exchange_index": 0,
                }
            )
        if create_rows:
            response = self.client.batch_create_orders(create_rows)
            errors = [item.get("error") for item in response.get("orders", []) if item.get("error")]
            if errors:
                raise RuntimeError("one or more quote orders were rejected: %s" % errors)

    def cancel_all(self) -> None:
        orders = [
            order
            for order in self.client.get_orders(self.ticker, "resting")
            if self.is_owned(order)
        ]
        if orders:
            self.client.batch_cancel_orders([str(order["order_id"]) for order in orders])

    def close(self, cancel_orders: bool = True) -> None:
        try:
            try:
                if cancel_orders:
                    self.cancel_all()
            finally:
                if self.order_group_id:
                    self.client.delete_order_group(self.order_group_id)
                    self.order_group_id = ""
        finally:
            self._release_lock()
