from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

import pandas as pd


class SignalType(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    EXIT = "EXIT"
    HOLD = "HOLD"


@dataclass(frozen=True)
class Signal:
    strategy_name: str
    strategy_version: str
    timestamp: str
    symbol: str
    signal: SignalType
    confidence: float = 0.0
    position_size_hint: float | None = None
    entry_reason: str = ""
    exit_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["signal"] = self.signal.value
        return data


class BaseStrategy(ABC):
    strategy_name: str
    strategy_version: str
    max_lookback_bars: int = 1

    @abstractmethod
    def generate_signal(self, history: pd.DataFrame, symbol: str, current_position: int) -> Signal:
        """Return a deterministic signal using only rows available up to history.iloc[-1]."""
        raise NotImplementedError


@dataclass
class FundingExtremeReversalV1(BaseStrategy):
    strategy_name: str = "funding_extreme_reversal"
    strategy_version: str = "v1"
    funding_zscore_window: int = 168
    extreme_threshold: float = 1.75
    momentum_window: int = 6
    exit_zscore: float = 0.5

    @property
    def max_lookback_bars(self) -> int:
        return max(self.funding_zscore_window, self.momentum_window) + 1

    def generate_signal(self, history: pd.DataFrame, symbol: str, current_position: int) -> Signal:
        row = history.iloc[-1]
        ts = row["decision_timestamp"].isoformat()
        if len(history) < self.max_lookback_bars or history["funding_rate"].tail(self.funding_zscore_window).isna().any():
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.HOLD, metadata={"reason": "warmup"})
        funding = history["funding_rate"].tail(self.funding_zscore_window)
        std = float(funding.std(ddof=0))
        z = 0.0 if std == 0 else (float(funding.iloc[-1]) - float(funding.mean())) / std
        momentum = float(row["close"] / history.iloc[-1 - self.momentum_window]["close"] - 1.0)
        meta = {"funding_zscore": z, "momentum": momentum, "params": self.__dict__.copy()}
        if current_position != 0 and abs(z) <= self.exit_zscore:
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.EXIT, 0.7, exit_reason="Funding extreme normalized", metadata=meta)
        if z >= self.extreme_threshold and momentum <= 0:
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.SHORT, min(abs(z) / 3, 1.0), entry_reason="Extremely positive funding with stalled/negative momentum", metadata=meta)
        if z <= -self.extreme_threshold and momentum >= 0:
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.LONG, min(abs(z) / 3, 1.0), entry_reason="Extremely negative funding with stalled/positive momentum", metadata=meta)
        return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.HOLD, metadata=meta)


@dataclass
class OIMomentumV1(BaseStrategy):
    strategy_name: str = "oi_momentum"
    strategy_version: str = "v1"
    lookback_hours: int = 6
    min_price_move: float = 0.005
    min_oi_move: float = 0.01

    @property
    def max_lookback_bars(self) -> int:
        return self.lookback_hours + 1

    def generate_signal(self, history: pd.DataFrame, symbol: str, current_position: int) -> Signal:
        row = history.iloc[-1]
        ts = row["decision_timestamp"].isoformat()
        if len(history) < self.max_lookback_bars or history["open_interest"].tail(self.max_lookback_bars).isna().any():
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.HOLD, metadata={"reason": "warmup"})
        prev = history.iloc[-1 - self.lookback_hours]
        price_ret = float(row["close"] / prev["close"] - 1.0)
        oi_ret = float(row["open_interest"] / prev["open_interest"] - 1.0)
        state = "PRICE_UP_OI_UP" if price_ret > 0 and oi_ret > 0 else "PRICE_UP_OI_DOWN" if price_ret > 0 and oi_ret < 0 else "PRICE_DOWN_OI_UP" if price_ret < 0 and oi_ret > 0 else "PRICE_DOWN_OI_DOWN"
        meta = {"price_return": price_ret, "oi_return": oi_ret, "state": state, "params": self.__dict__.copy()}
        if price_ret >= self.min_price_move and oi_ret >= self.min_oi_move:
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.LONG, 0.65, entry_reason="Price and OI rising together", metadata=meta)
        if price_ret <= -self.min_price_move and oi_ret >= self.min_oi_move:
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.SHORT, 0.65, entry_reason="Price falling while OI expands", metadata=meta)
        if current_position != 0 and oi_ret <= 0:
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.EXIT, 0.6, exit_reason="OI expansion no longer supports trend", metadata=meta)
        return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.HOLD, metadata=meta)


@dataclass
class OIDivergenceV1(BaseStrategy):
    """Conservative v1: EXIT / trade-avoidance filter, not a directional entry model."""

    strategy_name: str = "oi_divergence"
    strategy_version: str = "v1"
    lookback_hours: int = 6
    min_price_move: float = 0.005
    min_oi_drop: float = 0.01

    @property
    def max_lookback_bars(self) -> int:
        return self.lookback_hours + 1

    def generate_signal(self, history: pd.DataFrame, symbol: str, current_position: int) -> Signal:
        row = history.iloc[-1]
        ts = row["decision_timestamp"].isoformat()
        if len(history) < self.max_lookback_bars or history["open_interest"].tail(self.max_lookback_bars).isna().any():
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.HOLD, metadata={"reason": "warmup"})
        prev = history.iloc[-1 - self.lookback_hours]
        price_ret = float(row["close"] / prev["close"] - 1.0)
        oi_ret = float(row["open_interest"] / prev["open_interest"] - 1.0)
        up_divergence = price_ret >= self.min_price_move and oi_ret <= -self.min_oi_drop
        down_divergence = price_ret <= -self.min_price_move and oi_ret <= -self.min_oi_drop
        state = "SHORT_COVERING_RISK" if up_divergence else "LONG_LIQUIDATION_RISK" if down_divergence else "NONE"
        meta = {"price_return": price_ret, "oi_return": oi_ret, "divergence_state": state, "params": self.__dict__.copy()}
        if current_position > 0 and up_divergence:
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.EXIT, 0.7, exit_reason="Price up + OI down: possible short covering; long trend quality weak", metadata=meta)
        if current_position < 0 and down_divergence:
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.EXIT, 0.7, exit_reason="Price down + OI down: possible long liquidation; short trend quality weak", metadata=meta)
        return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.HOLD, metadata=meta)
