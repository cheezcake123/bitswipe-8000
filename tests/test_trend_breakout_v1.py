import pandas as pd

from strategy_arena.backtest import BacktestConfig, BacktestEngine
from strategy_arena.strategies import BaseStrategy, FundingExtremeReversalV1, OIMomentumV1, Signal, SignalType
from strategy_arena.trend_breakout_v1 import VolatilityAdjustedTrendBreakoutV1


def test_legacy_strategy_parameters_are_unchanged():
    funding = FundingExtremeReversalV1()
    oi = OIMomentumV1()
    assert (funding.funding_zscore_window, funding.extreme_threshold, funding.momentum_window, funding.exit_zscore) == (168, 1.75, 6, 0.5)
    assert (oi.lookback_hours, oi.min_price_move, oi.min_oi_move) == (6, 0.005, 0.01)


def test_trend_v1_uses_fixed_55_20_rules_and_reduces_size_in_high_volatility():
    timestamps = pd.date_range("2020-01-01 23:00:00+00:00", periods=200, freq="1D")
    close = pd.Series([100.0 + i * 0.1 for i in range(200)])
    close.iloc[-1] = 150.0
    frame = pd.DataFrame({
        "timestamp": timestamps,
        "close_time": timestamps + pd.Timedelta(minutes=59, seconds=59),
        "decision_timestamp": timestamps + pd.Timedelta(minutes=59, seconds=59),
        "symbol": "BTCUSDT",
        "open": close - 0.2,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": 1000.0,
    })
    strategy = VolatilityAdjustedTrendBreakoutV1(bars_per_day=1)
    signal = strategy.generate_signal(frame, "BTCUSDT", 0)
    assert strategy.entry_window_days == 55
    assert strategy.exit_window_days == 20
    assert signal.signal == SignalType.LONG
    assert signal.position_size_hint is not None
    assert 0.0 < signal.position_size_hint < 1.0


class HalfSizeOnce(BaseStrategy):
    strategy_name = "half_size_once"
    strategy_version = "v1"

    def generate_signal(self, history: pd.DataFrame, symbol: str, current_position: int) -> Signal:
        ts = pd.Timestamp(history.iloc[-1]["decision_timestamp"]).isoformat()
        if len(history) == 1:
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.LONG, 1.0, position_size_hint=0.5)
        return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.HOLD)


def test_common_engine_respects_position_size_hint_without_changing_leverage_rule():
    ts = pd.date_range("2026-01-01", periods=3, freq="1h", tz="UTC")
    data = pd.DataFrame({
        "timestamp": ts,
        "close_time": ts + pd.Timedelta(minutes=59, seconds=59),
        "decision_timestamp": ts + pd.Timedelta(minutes=59, seconds=59),
        "symbol": "BTCUSDT",
        "open": [100.0, 100.0, 101.0],
        "high": [101.0, 102.0, 102.0],
        "low": [99.0, 99.0, 100.0],
        "close": [100.0, 101.0, 101.0],
        "volume": [1.0, 1.0, 1.0],
        "funding_payment_rate": [0.0, 0.0, 0.0],
    })
    engine = BacktestEngine(BacktestConfig(initial_equity=10_000, position_size=1.0, leverage=2.0, taker_fee_rate=0.0, slippage_rate=0.0))
    result = engine.run(data, HalfSizeOnce())
    assert len(result.trades) == 1
    assert abs(float(result.trades.iloc[0]["units"]) - 100.0) < 1e-9
