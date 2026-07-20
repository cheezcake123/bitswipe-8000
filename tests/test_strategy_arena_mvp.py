from __future__ import annotations

import unittest

import pandas as pd

from strategy_arena.backtest import BacktestConfig, BacktestEngine
from strategy_arena.data import DataValidationError, build_research_dataset, validate_research_dataset
from strategy_arena.strategies import FundingExtremeReversalV1, OIDivergenceV1, OIMomentumV1


class StrategyArenaMVPTests(unittest.TestCase):
    def setUp(self):
        opens = pd.date_range("2026-06-01", periods=240, freq="1h", tz="UTC")
        self.ohlcv = pd.DataFrame({
            "timestamp": opens,
            "close_time": opens + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1),
            "symbol": "BTCUSDT",
            "source": "binance_usdm",
            "open": [100 + i * 0.1 for i in range(240)],
            "high": [101 + i * 0.1 for i in range(240)],
            "low": [99 + i * 0.1 for i in range(240)],
            "close": [100.2 + i * 0.1 for i in range(240)],
            "volume": 10.0,
        })
        funding_ts = pd.date_range("2026-05-31", periods=40, freq="8h", tz="UTC")
        self.funding = pd.DataFrame({
            "timestamp": funding_ts,
            "symbol": "BTCUSDT",
            "source": "binance_usdm",
            "funding_rate": [0.0001 + (i % 7) * 0.00002 for i in range(40)],
            "mark_price": 100.0,
        })
        oi_ts = pd.date_range("2026-06-01", periods=241, freq="1h", tz="UTC")
        self.oi = pd.DataFrame({
            "timestamp": oi_ts,
            "symbol": "BTCUSDT",
            "source": "binance_usdm",
            "open_interest": [1000 + i * 3 for i in range(241)],
            "open_interest_value": [100000 + i * 300 for i in range(241)],
        })

    def test_asof_alignment_never_uses_future_data(self):
        data = build_research_dataset(self.ohlcv, self.funding, self.oi, "BTCUSDT")
        validate_research_dataset(data)
        self.assertTrue((data["funding_available_at"] <= data["decision_timestamp"]).dropna().all())
        self.assertTrue((data["oi_available_at"] <= data["decision_timestamp"]).dropna().all())

    def test_validator_blocks_future_join(self):
        data = build_research_dataset(self.ohlcv, self.funding, self.oi, "BTCUSDT")
        data.loc[10, "oi_available_at"] = data.loc[10, "decision_timestamp"] + pd.Timedelta(hours=1)
        with self.assertRaises(DataValidationError):
            validate_research_dataset(data)

    def test_three_strategies_run_same_engine(self):
        data = build_research_dataset(self.ohlcv, self.funding, self.oi, "BTCUSDT")
        engine = BacktestEngine(BacktestConfig(initial_equity=10000, leverage=2))
        for strategy in [FundingExtremeReversalV1(), OIMomentumV1(), OIDivergenceV1()]:
            result = engine.run(data, strategy)
            self.assertEqual(result.strategy_name, strategy.strategy_name)
            self.assertIn("total_return", result.metrics)
            self.assertFalse(result.equity_curve.empty)


if __name__ == "__main__":
    unittest.main()
