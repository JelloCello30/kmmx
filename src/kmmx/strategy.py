from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Optional, Tuple

from .config import StrategyConfig
from .models import Incentive, ONE, ZERO, OrderBook, Quote, QuotePlan


COUNT_STEP = Decimal("0.01")


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))


def _round_price(value: Decimal, tick: Decimal, upward: bool = False) -> Decimal:
    if tick <= ZERO:
        return value
    rounding = ROUND_CEILING if upward else ROUND_FLOOR
    return (value / tick).to_integral_value(rounding=rounding) * tick


def _round_count(value: Decimal) -> Decimal:
    return max(ZERO, value.quantize(COUNT_STEP, rounding=ROUND_FLOOR))


class AdaptiveRewardStrategy:
    """Inventory-aware maker quotes with a conservative reward EV gate."""

    def __init__(self, config: StrategyConfig, cycle_seconds: float) -> None:
        self.config = config
        self.cycle_seconds = Decimal(str(cycle_seconds))
        self._fair: Optional[Decimal] = None
        self._variance = ZERO

    @property
    def fair_value(self) -> Optional[Decimal]:
        return self._fair

    @property
    def volatility(self) -> Decimal:
        return self._variance.sqrt() if self._variance > ZERO else ZERO

    def observe(self, book: OrderBook) -> Tuple[Decimal, Decimal]:
        micro_weight = Decimal(str(self.config.microprice_weight))
        observation = book.midpoint * (ONE - micro_weight) + book.microprice * micro_weight
        if self._fair is None:
            self._fair = observation
            return self._fair, ZERO

        alpha = Decimal(str(self.config.ewma_alpha))
        innovation = observation - self._fair
        self._variance = (ONE - alpha) * self._variance + alpha * innovation * innovation
        self._fair = (ONE - alpha) * self._fair + alpha * observation
        return self._fair, self.volatility

    def _economics(
        self,
        side: str,
        price: Decimal,
        fair: Decimal,
        volatility: Decimal,
        book: OrderBook,
        tick: Decimal,
        incentive: Optional[Incentive],
    ) -> Tuple[Decimal, Decimal, Decimal]:
        fill_probability = Decimal(str(self.config.expected_fill_probability))
        fill_probability = _clamp(fill_probability, ZERO, ONE)
        edge = fair - price if side == "bid" else price - fair
        adverse_cost = (
            tick * Decimal(str(self.config.base_adverse_selection_ticks))
            + volatility * Decimal(str(self.config.volatility_widening))
            + self.config.maker_fee_estimate_per_contract
        )
        trading_value = fill_probability * (edge - adverse_cost)

        reward_value = ZERO
        if incentive is not None and incentive.incentive_type == "liquidity":
            distance = book.distance_from_best(side, price, tick)
            rate = incentive.estimated_rate_per_contract_hour(
                Decimal(str(self.config.reward_competition_multiple))
            )
            reward_value = (
                rate * (self.cycle_seconds / Decimal("3600")) * incentive.score_multiplier(distance)
            )
        return trading_value + reward_value, reward_value, trading_value

    def make_plan(
        self,
        book: OrderBook,
        tick: Decimal,
        position: Decimal,
        incentive: Optional[Incentive],
    ) -> QuotePlan:
        reasons = []
        if not book.valid:
            return QuotePlan(book.ticker, book.midpoint, ZERO, None, None, ("invalid book",))

        fair, volatility = self.observe(book)
        max_position = self.config.max_position
        inventory_ratio = _clamp(position / max_position, -ONE, ONE)
        skew_ticks = int(
            (abs(inventory_ratio) * Decimal(self.config.inventory_skew_ticks)).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
        vol_ticks = int(
            (
                (volatility / tick) * Decimal(str(self.config.volatility_widening))
                if tick > ZERO
                else ZERO
            ).to_integral_value(rounding=ROUND_CEILING)
        )

        bid_price = book.best_bid
        ask_price = book.best_ask
        if inventory_ratio > ZERO:
            bid_price -= tick * skew_ticks
            ask_price = max(book.best_bid + tick, ask_price - tick * skew_ticks)
        elif inventory_ratio < ZERO:
            bid_price = min(book.best_ask - tick, bid_price + tick * skew_ticks)
            ask_price += tick * skew_ticks

        bid_price -= tick * vol_ticks
        ask_price += tick * vol_ticks
        bid_price = _round_price(bid_price, tick, upward=False)
        ask_price = _round_price(ask_price, tick, upward=True)
        bid_price = _clamp(bid_price, tick, ONE - tick * Decimal("2"))
        ask_price = _clamp(ask_price, tick * Decimal("2"), ONE - tick)

        if bid_price >= ask_price:
            reasons.append("quotes would cross")
            return QuotePlan(book.ticker, fair, volatility, None, None, tuple(reasons))

        size_skew = inventory_ratio * Decimal("0.75")
        bid_size = self.config.base_order_size * (ONE - size_skew)
        ask_size = self.config.base_order_size * (ONE + size_skew)
        bid_capacity = max(ZERO, max_position - position)
        ask_capacity = max(ZERO, max_position + position)
        bid_size = _round_count(min(bid_size, self.config.max_order_size, bid_capacity))
        ask_size = _round_count(min(ask_size, self.config.max_order_size, ask_capacity))

        if incentive is None and not self.config.enable_without_incentive:
            return QuotePlan(
                book.ticker,
                fair,
                volatility,
                None,
                None,
                ("no active liquidity incentive",),
            )

        minimum_ev = self.config.min_expected_value_per_contract
        bid = None
        if bid_size > ZERO:
            bid_ev, bid_reward, bid_trading = self._economics(
                "bid", bid_price, fair, volatility, book, tick, incentive
            )
            if bid_ev >= minimum_ev:
                bid = Quote("bid", bid_price, bid_size, bid_ev, bid_reward, bid_trading)
            else:
                reasons.append("bid EV below threshold")
        else:
            reasons.append("bid position capacity exhausted")

        ask = None
        if ask_size > ZERO:
            ask_ev, ask_reward, ask_trading = self._economics(
                "ask", ask_price, fair, volatility, book, tick, incentive
            )
            if ask_ev >= minimum_ev:
                ask = Quote("ask", ask_price, ask_size, ask_ev, ask_reward, ask_trading)
            else:
                reasons.append("ask EV below threshold")
        else:
            reasons.append("ask position capacity exhausted")

        if not bid and not ask and not reasons:
            reasons.append("no quote passed strategy filters")
        return QuotePlan(book.ticker, fair, volatility, bid, ask, tuple(reasons))
