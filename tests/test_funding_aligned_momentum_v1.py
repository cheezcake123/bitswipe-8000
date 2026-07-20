from __future__ import annotations

import unittest

import pandas as pd

from strategy_arena.funding_aligned_momentum_research import attach_known_funding
from strategy_arena.funding_aligned_momentum_v1 import FundingAlignedMomentumV1
from strategy_arena.funding_carry_v1 import FundingCarryV1Config
from strategy_arena.strategies import FundingExtremeReversalV1, OIMomentumV1, SignalType


class FundingAlignedMomentumV1Tests(unittest.TestCase):
    def test_frozen_v1_defaults(self):
        strategy = FundingAlignedMomentumV1()
        self.assertEqual(strategy.momentum_hours, 168)
        self.assertEqual(strategy.direction_mode, "LONG_ONLY")
        self.assertEqual(strategy.max_leverage, 2.0)

    def test_existing_strategy_parameters_remain_frozen(self):
        funding = FundingExtremeReversalV1()
        oi = OIMomentumV1()
        carry = FundingCarryV1Config()
        self.assertEqual(funding.funding_zscore_window, 168)
        self.assertEqual(funding.extreme_threshold, 1.75)
        self.assertEqual(funding.momentum_window, 6)
        self.assertEqual(funding.exit_zscore, 0.5)
        self.assertEqual(oi.lookback_hours, 6)
        self.assertEqual(oi.min_price_move, 0.005)
        self.assertEqual(oi.min_oi_move, 0.01)
        self.assertEqual(carry.entry_funding_rate, 0.0002)

    def test_backward_only_known_funding(self):
        hourly = pd.DataFrame({
            "timestamp": pd.to_datetime(["2026-01-01T07:00:00Z", "2026-01-01T08:00:00Z"]),
            "decision_timestamp": pd.to_datetime(["2026-01-01T07:59:59Z", "2026-01-01T08:59:59Z"]),
            "open": [100.0, 101.0],
            "high": [102.0, 103.0],
            "low": [99.0, 100.0],
            "close": [101.0, 102.0],
            "volume": [1.0, 1.0],
            "symbol": ["BTCUSDT", "BTCUSDT"],
            "source": ["test", "test"],
        })
        funding = pd.DataFrame({
            "timestamp": pd.to_datetime(["2026-01-01T00:00:00Z", "2026-01-01T08:00:00Z"]),
            "funding_rate": [0.0001, -0.0002],
        })
        merged = attach_known_funding(hourly, funding)
        self.assertAlmostEqual(float(merged.iloc[0]["known_funding_rate"]), 0.0001)
        self.assertAlmostEqual(float(merged.iloc[1]["known_funding_rate"]), -0.0002)

    def test_long_entry_requires_positive_momentum_and_negative_known_funding(self):
        strategy = FundingAlignedMomentumV1()
        row = pd.DataFrame([{
            "timestamp": pd.Timestamp("2026-01-01T00:00:00Z"),
            "decision_timestamp": pd.Timestamp("2026-01-01T00:59:59Z"),
            "fam_ready": True,
            "fam_momentum_return": 0.05,
            "fam_known_funding_rate": -0.0001,
            "fam_known_funding_timestamp": pd.Timestamp("2026-01-01T00:00:00Z"),
        }])
        signal = strategy.generate_signal(row, "BTCUSDT", 0)
        self.assertEqual(signal.signal, SignalType.LONG)

    def test_default_v1_does_not_take_short_signal(self):
        strategy = FundingAlignedMomentumV1()
        row = pd.DataFrame([{
            "timestamp": pd.Timestamp("2026-01-01T00:00:00Z"),
            "decision_timestamp": pd.Timestamp("2026-01-01T00:59:59Z"),
            "fam_ready": True,
            "fam_momentum_return": -0.05,
            "fam_known_funding_rate": 0.0001,
            "fam_known_funding_timestamp": pd.Timestamp("2026-01-01T00:00:00Z"),
        }])
        signal = strategy.generate_signal(row, "BTCUSDT", 0)
        self.assertEqual(signal.signal, SignalType.HOLD)


if __name__ == "__main__":
    unittest.main()
