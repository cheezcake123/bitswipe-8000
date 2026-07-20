from __future__ import annotations

import pandas as pd

from strategy_arena.funding_extreme_long_validation import (
    DirectionFilteredFundingExtreme,
    build_long_funding_dataset,
    validate_no_lookahead,
)
from strategy_arena.strategies import FundingExtremeReversalV1, SignalType


def _hourly() -> pd.DataFrame:
    ts = pd.date_range("2026-01-01T00:00:00Z", periods=4, freq="1h")
    return pd.DataFrame({
        "timestamp": ts,
        "close_time": ts + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1),
        "decision_timestamp": ts + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1),
        "symbol": "BTCUSDT",
        "source": "test",
        "open": [100.0, 101.0, 102.0, 103.0],
        "high": [102.0, 103.0, 104.0, 105.0],
        "low": [99.0, 100.0, 101.0, 102.0],
        "close": [101.0, 102.0, 103.0, 104.0],
        "volume": [1.0, 1.0, 1.0, 1.0],
    })


def _funding() -> pd.DataFrame:
    return pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-01-01T00:00:00Z", "2026-01-01T02:00:00Z"], utc=True),
        "funding_rate": [0.0001, -0.0002],
    })


def test_frozen_parameters_unchanged() -> None:
    s = FundingExtremeReversalV1()
    assert s.funding_zscore_window == 168
    assert s.extreme_threshold == 1.75
    assert s.momentum_window == 6
    assert s.exit_zscore == 0.5


def test_backward_only_funding_alignment_and_actual_payment_hour() -> None:
    data = build_long_funding_dataset(_hourly(), _funding(), "BTCUSDT")
    assert data.loc[0, "funding_rate"] == 0.0001
    # The 02:00 funding event must not be visible at the 01:59:59.999 decision time.
    assert data.loc[1, "funding_rate"] == 0.0001
    # It becomes available for the candle that closes after 02:00.
    assert data.loc[2, "funding_rate"] == -0.0002
    assert data.loc[0, "funding_payment_rate"] == 0.0001
    assert data.loc[2, "funding_payment_rate"] == -0.0002
    report = validate_no_lookahead(data, _funding())
    assert report["pass"] is True
    assert report["future_funding_rows"] == 0


def test_direction_adapter_only_suppresses_opposite_entries(monkeypatch) -> None:
    adapter = DirectionFilteredFundingExtreme("LONG_ONLY")

    def fake(self, history, symbol, current_position):
        from strategy_arena.strategies import Signal
        return Signal(self.strategy_name, self.strategy_version, history.iloc[-1]["decision_timestamp"].isoformat(), symbol, SignalType.SHORT, entry_reason="test")

    monkeypatch.setattr(FundingExtremeReversalV1, "generate_signal", fake)
    history = _hourly().iloc[:1].copy()
    signal = adapter.generate_signal(history, "BTCUSDT", 0)
    assert signal.signal == SignalType.HOLD
