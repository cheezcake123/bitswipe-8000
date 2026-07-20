from __future__ import annotations

import pandas as pd

from strategy_arena.funding_carry_v1 import FundingCarryV1Config, simulate
from strategy_arena.strategies import FundingExtremeReversalV1, OIMomentumV1


def _prices(minutes: int = 120) -> pd.DataFrame:
    ts = pd.date_range("2024-01-01", periods=minutes, freq="1min", tz="UTC")
    spot = [100.0 + i * 0.001 for i in range(minutes)]
    perp = [100.1 + i * 0.001 for i in range(minutes)]
    return pd.DataFrame({
        "timestamp": ts,
        "spot_open": spot,
        "spot_high": [x + 0.02 for x in spot],
        "spot_close": spot,
        "perp_open": perp,
        "perp_high": [x + 0.02 for x in perp],
        "perp_close": perp,
    })


def test_trigger_funding_is_not_collected_and_execution_is_next_bar_with_leg_delay():
    data = _prices()
    funding = pd.DataFrame({
        "timestamp": [data.iloc[10]["timestamp"], data.iloc[20]["timestamp"], data.iloc[30]["timestamp"]],
        "funding_rate": [0.0002, 0.0001, -0.0001],
        "mark_price": [100.11, 100.12, 100.13],
    })
    trades, _, metrics = simulate(data, funding, FundingCarryV1Config())
    assert len(trades) == 1
    trade = trades.iloc[0]
    assert trade["signal_time"] == data.iloc[10]["timestamp"]
    assert trade["entry_spot_time"] == data.iloc[11]["timestamp"]
    assert trade["entry_perp_time"] == data.iloc[12]["timestamp"]
    assert trade["exit_signal_time"] == data.iloc[30]["timestamp"]
    assert trade["exit_spot_time"] == data.iloc[31]["timestamp"]
    assert trade["exit_perp_time"] == data.iloc[32]["timestamp"]
    # Trigger payment at minute 10 is in the past at entry and must not be booked.
    assert trade["funding_events"] == 2
    assert metrics["number_of_carry_episodes"] == 1


def test_v1_threshold_is_fixed_and_existing_strategy_parameters_are_unchanged():
    cfg = FundingCarryV1Config()
    assert cfg.entry_funding_rate == 0.0002
    assert cfg.exit_funding_rate == 0.0
    assert cfg.perp_leg_delay_bars == 1

    funding_reversal = FundingExtremeReversalV1()
    assert funding_reversal.funding_zscore_window == 168
    assert funding_reversal.extreme_threshold == 1.75
    assert funding_reversal.momentum_window == 6
    assert funding_reversal.exit_zscore == 0.5

    oi = OIMomentumV1()
    assert oi.lookback_hours == 6
    assert oi.min_price_move == 0.005
    assert oi.min_oi_move == 0.01
