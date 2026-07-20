from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

import strategy_arena.funding_extreme_long_validation as validation
from strategy_arena.backtest import BacktestEngine
from strategy_arena.strategies import FundingExtremeReversalV1, Signal, SignalType


class _ILocView:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def __getitem__(self, index: int):
        return self.rows[index]


class _RollingHistoryView:
    """Minimal dataframe-like view used only to execute the original v1 method faster."""

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.iloc = _ILocView(rows)

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, column: str) -> pd.Series:
        return pd.Series([row.get(column) for row in self.rows])


@dataclass
class BufferedDirectionFilteredFundingExtreme:
    """Runtime-only adapter that calls the frozen original v1 on its declared lookback.

    FundingExtremeReversalV1.generate_signal itself remains the only implementation of
    z-score, momentum, entry, and exit logic. The adapter retains exactly the strategy's
    declared 169-bar lookback and exposes a tiny dataframe-like view, avoiding repeated
    copies of multi-year history during independent validation runs.
    """

    direction_mode: str = "BOTH"
    _rows: list[dict] = field(default_factory=list, init=False)
    uses_precomputed_features: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        self.inner = FundingExtremeReversalV1()
        self.strategy_name = self.inner.strategy_name
        self.strategy_version = self.inner.strategy_version
        self.max_lookback_bars = self.inner.max_lookback_bars

    def generate_signal(self, history: pd.DataFrame, symbol: str, current_position: int) -> Signal:
        self._rows.append(history.iloc[-1].to_dict())
        if len(self._rows) > self.max_lookback_bars:
            self._rows.pop(0)
        signal = self.inner.generate_signal(_RollingHistoryView(self._rows), symbol, current_position)
        if current_position == 0:
            if self.direction_mode == "LONG_ONLY" and signal.signal == SignalType.SHORT:
                return Signal(signal.strategy_name, signal.strategy_version, signal.timestamp, symbol, SignalType.HOLD, metadata={**signal.metadata, "direction_filter": "LONG_ONLY"})
            if self.direction_mode == "SHORT_ONLY" and signal.signal == SignalType.LONG:
                return Signal(signal.strategy_name, signal.strategy_version, signal.timestamp, symbol, SignalType.HOLD, metadata={**signal.metadata, "direction_filter": "SHORT_ONLY"})
        return signal


def fast_run_three_stage(data: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, direction: str):
    warmup = FundingExtremeReversalV1().max_lookback_bars
    warmup_start = pd.Timestamp(start) - pd.Timedelta(hours=warmup)
    work = data[(data["timestamp"] >= warmup_start) & (data["timestamp"] <= end)].copy().reset_index(drop=True)

    price_data = work.copy()
    price_data["funding_payment_rate"] = 0.0
    price = BacktestEngine(validation.PRICE_CONFIG).run(price_data, BufferedDirectionFilteredFundingExtreme(direction), start, end)
    funding_gross = BacktestEngine(validation.PRICE_CONFIG).run(work, BufferedDirectionFilteredFundingExtreme(direction), start, end)
    net = BacktestEngine(validation.NET_CONFIG).run(work, BufferedDirectionFilteredFundingExtreme(direction), start, end)
    return price, funding_gross, net


def main() -> None:
    validation.run_three_stage = fast_run_three_stage
    validation.main()


if __name__ == "__main__":
    main()
