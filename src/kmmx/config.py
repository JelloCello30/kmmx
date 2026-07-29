from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .models import decimal


@dataclass(frozen=True)
class ApiConfig:
    environment: str = "production"
    timeout_seconds: float = 10.0
    max_retries: int = 3

    @property
    def base_url(self) -> str:
        if self.environment == "demo":
            return "https://external-api.demo.kalshi.co/trade-api/v2"
        return "https://external-api.kalshi.com/trade-api/v2"


@dataclass(frozen=True)
class StrategyConfig:
    base_order_size: Decimal = Decimal("10")
    max_order_size: Decimal = Decimal("25")
    max_position: Decimal = Decimal("100")
    ewma_alpha: float = 0.35
    microprice_weight: float = 0.65
    inventory_skew_ticks: int = 3
    volatility_widening: float = 1.5
    base_adverse_selection_ticks: float = 0.5
    maker_fee_estimate_per_contract: Decimal = Decimal("0")
    expected_fill_probability: float = 0.03
    reward_competition_multiple: float = 4.0
    min_expected_value_per_contract: Decimal = Decimal("0")
    enable_without_incentive: bool = False


@dataclass(frozen=True)
class RiskConfig:
    max_market_capital: Decimal = Decimal("50")
    max_portfolio_capital: Decimal = Decimal("250")
    max_daily_loss: Decimal = Decimal("15")
    max_consecutive_api_errors: int = 5
    max_book_age_seconds: float = 8.0
    min_seconds_to_close: int = 900
    max_mid_move_ticks: int = 5
    max_open_orders_per_market: int = 2
    max_fills_per_15_seconds: int = 25


@dataclass(frozen=True)
class RuntimeConfig:
    poll_interval_seconds: float = 2.0
    order_ttl_seconds: int = 12
    paper_starting_cash: Decimal = Decimal("1000")
    cancel_orders_on_exit: bool = True
    kill_switch_file: str = ".kmmx-stop"
    state_file: str = ".kmmx-state.json"
    approved_live_tickers: Tuple[str, ...] = ()


@dataclass(frozen=True)
class BotConfig:
    api: ApiConfig = field(default_factory=ApiConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)

    def validate(self) -> None:
        if self.api.environment not in {"production", "demo"}:
            raise ValueError("api.environment must be 'production' or 'demo'")
        if not (0 < self.strategy.ewma_alpha <= 1):
            raise ValueError("strategy.ewma_alpha must be in (0, 1]")
        if not (0 <= self.strategy.microprice_weight <= 1):
            raise ValueError("strategy.microprice_weight must be in [0, 1]")
        if self.strategy.base_order_size <= 0 or self.strategy.max_order_size <= 0:
            raise ValueError("order sizes must be positive")
        if self.strategy.base_order_size > self.strategy.max_order_size:
            raise ValueError("base_order_size cannot exceed max_order_size")
        if self.strategy.max_position <= 0:
            raise ValueError("max_position must be positive")
        if self.strategy.maker_fee_estimate_per_contract < 0:
            raise ValueError("maker_fee_estimate_per_contract cannot be negative")
        if self.risk.max_market_capital <= 0 or self.risk.max_portfolio_capital <= 0:
            raise ValueError("capital limits must be positive")
        if self.runtime.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if self.runtime.order_ttl_seconds <= self.runtime.poll_interval_seconds:
            raise ValueError("order_ttl_seconds must exceed poll_interval_seconds")


def _section(payload: Dict[str, Any], name: str) -> Dict[str, Any]:
    value = payload.get(name, {})
    if not isinstance(value, dict):
        raise ValueError("configuration section %s must be an object" % name)
    return value


def load_config(path: Optional[str] = None) -> BotConfig:
    payload: Dict[str, Any] = {}
    if path:
        with Path(path).expanduser().open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

    api_raw = _section(payload, "api")
    strategy_raw = _section(payload, "strategy")
    risk_raw = _section(payload, "risk")
    runtime_raw = _section(payload, "runtime")
    approved_live_tickers = runtime_raw.get("approved_live_tickers", [])
    if not isinstance(approved_live_tickers, list):
        raise ValueError("runtime.approved_live_tickers must be an array")

    api = ApiConfig(
        environment=str(api_raw.get("environment", "production")),
        timeout_seconds=float(api_raw.get("timeout_seconds", 10.0)),
        max_retries=int(api_raw.get("max_retries", 3)),
    )
    strategy = StrategyConfig(
        base_order_size=decimal(strategy_raw.get("base_order_size"), "10"),
        max_order_size=decimal(strategy_raw.get("max_order_size"), "25"),
        max_position=decimal(strategy_raw.get("max_position"), "100"),
        ewma_alpha=float(strategy_raw.get("ewma_alpha", 0.35)),
        microprice_weight=float(strategy_raw.get("microprice_weight", 0.65)),
        inventory_skew_ticks=int(strategy_raw.get("inventory_skew_ticks", 3)),
        volatility_widening=float(strategy_raw.get("volatility_widening", 1.5)),
        base_adverse_selection_ticks=float(strategy_raw.get("base_adverse_selection_ticks", 0.5)),
        maker_fee_estimate_per_contract=decimal(
            strategy_raw.get("maker_fee_estimate_per_contract"), "0"
        ),
        expected_fill_probability=float(strategy_raw.get("expected_fill_probability", 0.03)),
        reward_competition_multiple=float(strategy_raw.get("reward_competition_multiple", 4.0)),
        min_expected_value_per_contract=decimal(
            strategy_raw.get("min_expected_value_per_contract"), "0"
        ),
        enable_without_incentive=bool(strategy_raw.get("enable_without_incentive", False)),
    )
    risk = RiskConfig(
        max_market_capital=decimal(risk_raw.get("max_market_capital"), "50"),
        max_portfolio_capital=decimal(risk_raw.get("max_portfolio_capital"), "250"),
        max_daily_loss=decimal(risk_raw.get("max_daily_loss"), "15"),
        max_consecutive_api_errors=int(risk_raw.get("max_consecutive_api_errors", 5)),
        max_book_age_seconds=float(risk_raw.get("max_book_age_seconds", 8.0)),
        min_seconds_to_close=int(risk_raw.get("min_seconds_to_close", 900)),
        max_mid_move_ticks=int(risk_raw.get("max_mid_move_ticks", 5)),
        max_open_orders_per_market=int(risk_raw.get("max_open_orders_per_market", 2)),
        max_fills_per_15_seconds=int(risk_raw.get("max_fills_per_15_seconds", 25)),
    )
    runtime = RuntimeConfig(
        poll_interval_seconds=float(runtime_raw.get("poll_interval_seconds", 2.0)),
        order_ttl_seconds=int(runtime_raw.get("order_ttl_seconds", 12)),
        paper_starting_cash=decimal(runtime_raw.get("paper_starting_cash"), "1000"),
        cancel_orders_on_exit=bool(runtime_raw.get("cancel_orders_on_exit", True)),
        kill_switch_file=str(runtime_raw.get("kill_switch_file", ".kmmx-stop")),
        state_file=str(runtime_raw.get("state_file", ".kmmx-state.json")),
        approved_live_tickers=tuple(str(item) for item in approved_live_tickers),
    )
    config = BotConfig(api=api, strategy=strategy, risk=risk, runtime=runtime)
    config.validate()
    return config
