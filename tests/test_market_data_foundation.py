from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from strategy_arena.basis import build_spot_perpetual_basis_dataset
from strategy_arena.market_store import AppendOnlyMarketStore
from strategy_arena.strategies import FundingExtremeReversalV1, OIMomentumV1


class MarketDataFoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "store"
        self.store = AppendOnlyMarketStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_append_only_deduplicates_without_rewriting_existing_batch(self):
        frame = pd.DataFrame({
            "timestamp": pd.to_datetime(["2026-07-01T00:00:00Z", "2026-07-01T00:01:00Z"]),
            "symbol": ["BTCUSDT", "BTCUSDT"],
            "source": ["binance_usdm_rest", "binance_usdm_rest"],
            "price": [100.0, 101.0],
        })
        first = self.store.append("perpetual_price", frame)
        files_after_first = sorted(self.root.rglob("part-*.parquet"))
        second = self.store.append("perpetual_price", frame)
        files_after_second = sorted(self.root.rglob("part-*.parquet"))

        self.assertEqual(first["written"], 2)
        self.assertEqual(second["written"], 0)
        self.assertEqual(second["duplicates"], 2)
        self.assertEqual(files_after_first, files_after_second)
        self.assertEqual(len(self.store.read("perpetual_price", symbol="BTCUSDT")), 2)

    def test_liquidation_event_id_preserves_distinct_same_timestamp_events(self):
        ts = pd.Timestamp("2026-07-01T00:00:00Z")
        frame = pd.DataFrame({
            "timestamp": [ts, ts],
            "symbol": ["BTCUSDT", "BTCUSDT"],
            "source": ["binance_usdm_forceorder_ws", "binance_usdm_forceorder_ws"],
            "event_id": ["a", "b"],
            "liquidation_notional": [1000.0, 2000.0],
        })
        result = self.store.append("liquidation", frame)
        self.assertEqual(result["written"], 2)
        self.assertEqual(len(self.store.read("liquidation", symbol="BTCUSDT")), 2)

    def test_basis_fields_are_kept_separate_and_backward_aligned(self):
        times = pd.date_range("2026-07-01T00:00:00Z", periods=3, freq="1min")
        datasets = {
            "perpetual_price": ("binance_usdm_rest", [101.0, 102.0, 103.0]),
            "spot_price": ("binance_spot_rest", [100.0, 100.0, 100.0]),
            "mark_price": ("binance_usdm_rest", [100.5, 101.5, 102.5]),
            "index_price": ("binance_usdm_rest", [100.2, 101.0, 102.0]),
        }
        for dataset, (source, prices) in datasets.items():
            self.store.append(dataset, pd.DataFrame({
                "timestamp": times,
                "symbol": "BTCUSDT",
                "source": source,
                "price": prices,
            }))

        basis = build_spot_perpetual_basis_dataset(self.store, "BTCUSDT")
        self.assertEqual(len(basis), 3)
        self.assertAlmostEqual(float(basis.iloc[0]["spot_perp_basis_abs"]), 1.0)
        self.assertAlmostEqual(float(basis.iloc[0]["mark_index_basis_abs"]), 0.3)
        self.assertIn("spot_perp_basis_pct", basis.columns)
        self.assertIn("mark_index_basis_pct", basis.columns)

    def test_existing_v1_strategy_parameters_are_unchanged(self):
        funding = FundingExtremeReversalV1()
        oi = OIMomentumV1()
        self.assertEqual(funding.funding_zscore_window, 168)
        self.assertEqual(funding.extreme_threshold, 1.75)
        self.assertEqual(funding.momentum_window, 6)
        self.assertEqual(funding.exit_zscore, 0.5)
        self.assertEqual(oi.lookback_hours, 6)
        self.assertEqual(oi.min_price_move, 0.005)
        self.assertEqual(oi.min_oi_move, 0.01)


if __name__ == "__main__":
    unittest.main()
