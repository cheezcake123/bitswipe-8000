import pandas as pd

from strategy_arena.funding_carry_v1 import FundingCarryV1Config
from strategy_arena.strategies import FundingExtremeReversalV1, OIMomentumV1, SignalType
from strategy_arena.volatility_compression_v1 import VolatilityCompressionBreakoutV1


def _row(**overrides):
    base = {
        "timestamp": pd.Timestamp("2026-01-01T12:00:00Z"),
        "close_time": pd.Timestamp("2026-01-01T12:59:59.999Z"),
        "decision_timestamp": pd.Timestamp("2026-01-01T12:59:59.999Z"),
        "symbol": "BTCUSDT",
        "close": 99.0,
        "vc_ready": True,
        "vc_atr_pct": 0.01,
        "vc_compression_threshold": 0.012,
        "vc_range_upper": 105.0,
        "vc_range_lower": 100.0,
        "vc_entry_long": False,
        "vc_entry_short": True,
    }
    base.update(overrides)
    return pd.DataFrame([base])


def test_frozen_v1_defaults_and_existing_strategy_parameters_unchanged():
    v1 = VolatilityCompressionBreakoutV1()
    assert v1.direction_mode == "SHORT_ONLY"
    assert v1.compression_percentile == 0.20
    assert v1.percentile_lookback_hours == 90 * 24
    assert v1.atr_window_hours == 24
    assert v1.range_window_hours == 24
    assert v1.max_holding_hours == 24
    assert v1.max_leverage == 2.0

    funding = FundingExtremeReversalV1()
    assert funding.funding_zscore_window == 168
    assert funding.extreme_threshold == 1.75
    assert funding.momentum_window == 6
    assert funding.exit_zscore == 0.5

    oi = OIMomentumV1()
    assert oi.lookback_hours == 6
    assert oi.min_price_move == 0.005
    assert oi.min_oi_move == 0.01

    carry = FundingCarryV1Config()
    assert carry.entry_funding_rate == 0.0002
    assert carry.exit_funding_rate == 0.0


def test_short_only_v1_emits_short_on_precomputed_atr_compression_breakout():
    strategy = VolatilityCompressionBreakoutV1()
    signal = strategy.generate_signal(_row(), "BTCUSDT", 0)
    assert signal.signal == SignalType.SHORT
    assert "ATR% compression" in signal.entry_reason


def test_short_only_v1_does_not_take_long_breakout():
    strategy = VolatilityCompressionBreakoutV1()
    frame = _row(close=106.0, vc_entry_short=False, vc_entry_long=True)
    signal = strategy.generate_signal(frame, "BTCUSDT", 0)
    assert signal.signal == SignalType.HOLD


def test_range_reentry_exits_existing_short():
    strategy = VolatilityCompressionBreakoutV1()
    first = strategy.generate_signal(_row(), "BTCUSDT", 0)
    assert first.signal == SignalType.SHORT

    filled = _row(
        timestamp=pd.Timestamp("2026-01-01T13:00:00Z"),
        close_time=pd.Timestamp("2026-01-01T13:59:59.999Z"),
        decision_timestamp=pd.Timestamp("2026-01-01T13:59:59.999Z"),
        close=99.0,
        vc_entry_short=False,
    )
    assert strategy.generate_signal(filled, "BTCUSDT", -1).signal == SignalType.HOLD

    reentry = _row(
        timestamp=pd.Timestamp("2026-01-01T14:00:00Z"),
        close_time=pd.Timestamp("2026-01-01T14:59:59.999Z"),
        decision_timestamp=pd.Timestamp("2026-01-01T14:59:59.999Z"),
        close=100.5,
        vc_entry_short=False,
    )
    signal = strategy.generate_signal(reentry, "BTCUSDT", -1)
    assert signal.signal == SignalType.EXIT
    assert "re-entered original range" in signal.exit_reason
