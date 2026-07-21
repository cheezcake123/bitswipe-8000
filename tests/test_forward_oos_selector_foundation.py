from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from strategy_arena.forward_oos_league_runner import _common_results, build_forward_market_context
from strategy_arena.forward_oos_live_runner import run_live_snapshot
from strategy_arena.forward_oos_runtime import validate_frozen_runtime_parameters
from strategy_arena.forward_oos_selector_foundation import (
    FORWARD_OOS_START_UTC,
    OPERATIONAL_FORWARD_OOS_START_REASON,
    OPERATIONAL_FORWARD_OOS_START_UTC,
    PLANNED_FORWARD_OOS_START_UTC,
    AppendOnlyLeagueStore,
    ConflictManager,
    PositionRequest,
    RegimeDetectorV1,
    SignalOverlapAnalyzer,
    StrategySelectorFoundation,
    load_default_registry,
)
from strategy_arena.market_store import AppendOnlyMarketStore


def _hourly(funding_rate: float) -> pd.DataFrame:
    ts = pd.date_range("2026-06-01T00:00:00Z", periods=24 * 40, freq="1h")
    close = pd.Series(range(len(ts)), dtype=float) + 100.0
    return pd.DataFrame({
        "timestamp": ts,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": 1.0,
        "funding_rate": funding_rate,
    })


def _engine_hourly() -> tuple[pd.DataFrame, pd.DataFrame]:
    ts = pd.date_range("2026-07-10T00:00:00Z", periods=24 * 20, freq="1h")
    idx = np.arange(len(ts), dtype=float)
    close = 100.0 + idx * 0.05
    hourly = pd.DataFrame({
        "timestamp": ts,
        "close_time": ts + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1),
        "decision_timestamp": ts + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1),
        "symbol": "BTCUSDT",
        "source": "test",
        "open": close - 0.02,
        "high": close + 0.10,
        "low": close - 0.10,
        "close": close,
        "volume": 1.0,
        "funding_rate": 0.0001,
        "funding_available_at": ts,
        "funding_payment_rate": 0.0,
        "open_interest": 1_000.0 + idx,
        "oi_available_at": ts,
    })
    funding_ts = pd.date_range(ts.min(), ts.max(), freq="8h")
    funding = pd.DataFrame({"timestamp": funding_ts, "funding_rate": 0.0001})
    return hourly, funding


def test_registry_policy_and_frozen_runtime_parameters() -> None:
    registry = load_default_registry()
    assert [record.key for record in registry.selector_eligible()] == ["funding_carry:v1"]
    assert all(not record.allowed_for_paper for record in registry.records.values())
    assert registry.records["funding_carry:v1"].parameters["entry_funding_rate"] == 0.0002
    assert registry.records["funding_extreme_reversal:v1"].parameters == {
        "funding_zscore_window": 168,
        "extreme_threshold": 1.75,
        "momentum_window": 6,
        "exit_zscore": 0.5,
    }
    assert registry.records["oi_momentum:v1"].parameters == {
        "lookback_hours": 6,
        "min_price_move": 0.005,
        "min_oi_move": 0.01,
    }
    assert validate_frozen_runtime_parameters(registry)["pass"] is True


def test_planned_and_operational_forward_oos_boundaries_are_frozen(tmp_path) -> None:
    store = AppendOnlyLeagueStore(tmp_path / "league")
    metadata = json.loads((tmp_path / "league" / "league_metadata.json").read_text())
    assert PLANNED_FORWARD_OOS_START_UTC == pd.Timestamp("2026-07-01T00:00:00Z")
    assert OPERATIONAL_FORWARD_OOS_START_UTC == pd.Timestamp("2026-07-21T15:00:00Z")
    assert FORWARD_OOS_START_UTC == OPERATIONAL_FORWARD_OOS_START_UTC
    assert pd.Timestamp(metadata["planned_forward_oos_start_utc"]) == PLANNED_FORWARD_OOS_START_UTC
    assert pd.Timestamp(metadata["operational_forward_oos_start_utc"]) == OPERATIONAL_FORWARD_OOS_START_UTC
    assert metadata["operational_start_reason"] == OPERATIONAL_FORWARD_OOS_START_REASON
    assert pd.Timestamp(metadata["forward_oos_start_utc"]) == OPERATIONAL_FORWARD_OOS_START_UTC
    assert metadata["orders_enabled"] is False
    assert metadata["paper_trading_enabled"] is False
    assert metadata["auto_promotion_enabled"] is False
    with pytest.raises(ValueError):
        store.append(
            "signals",
            [{"timestamp": "2026-07-21T14:59:59Z", "strategy_name": "x", "signal": "HOLD"}],
        )
    assert store.append(
        "signals",
        [{"timestamp": "2026-07-21T15:00:00Z", "strategy_name": "x", "signal": "HOLD"}],
    ) is not None


def test_legacy_planned_metadata_migrates_only_before_any_league_records(tmp_path) -> None:
    root = tmp_path / "league"
    root.mkdir(parents=True)
    (root / "league_metadata.json").write_text(
        json.dumps(
            {
                "historical_backtest_cutoff_utc": "2026-06-30T23:59:59.999+00:00",
                "forward_oos_start_utc": PLANNED_FORWARD_OOS_START_UTC.isoformat(),
                "immutable_start": True,
                "orders_enabled": False,
                "paper_trading_enabled": False,
                "auto_promotion_enabled": False,
            }
        )
    )
    AppendOnlyLeagueStore(root)
    metadata = json.loads((root / "league_metadata.json").read_text())
    assert metadata["legacy_planned_metadata_migrated"] is True
    assert metadata["legacy_forward_oos_start_utc"] == PLANNED_FORWARD_OOS_START_UTC.isoformat()
    assert pd.Timestamp(metadata["operational_forward_oos_start_utc"]) == OPERATIONAL_FORWARD_OOS_START_UTC
    assert metadata["metadata_migration_reason"] == OPERATIONAL_FORWARD_OOS_START_REASON


def test_post_operational_archive_backfill_is_not_forward_oos(tmp_path) -> None:
    market_root = tmp_path / "market"
    market = AppendOnlyMarketStore(market_root)
    ts = pd.Timestamp("2026-07-22T00:00:00Z")
    archive_row = pd.DataFrame([{
        "timestamp": ts,
        "close_time": ts + pd.Timedelta(minutes=1) - pd.Timedelta(milliseconds=1),
        "symbol": "BTCUSDT",
        "source": "binance_vision_usdm",
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 1.0,
    }])
    market.append("futures_ohlcv", archive_row)
    result = run_live_snapshot(str(market_root), str(tmp_path / "league"))
    assert result["accepted_forward_source"] == "binance_usdm_rest"
    assert result["forward_live_rows_available"] == 0


def test_pre_operational_live_timestamp_rows_are_warmup_only_not_forward_scored(tmp_path) -> None:
    market = AppendOnlyMarketStore(tmp_path / "market")
    pre = pd.date_range("2026-07-21T14:00:00Z", periods=60, freq="1min")
    operational = pd.date_range("2026-07-21T15:00:00Z", periods=60, freq="1min")
    ts = pre.append(operational)
    close = 100.0 + np.arange(len(ts), dtype=float) * 0.001
    frame = pd.DataFrame(
        {
            "timestamp": ts,
            "close_time": ts + pd.Timedelta(minutes=1) - pd.Timedelta(milliseconds=1),
            "symbol": "BTCUSDT",
            "source": "binance_usdm_rest",
            "open": close,
            "high": close + 0.01,
            "low": close - 0.01,
            "close": close,
            "volume": 1.0,
        }
    )
    market.append("futures_ohlcv", frame)
    context = build_forward_market_context(market)
    forward = context["hourly"][context["hourly"]["timestamp"] >= OPERATIONAL_FORWARD_OOS_START_UTC]
    assert list(forward["timestamp"]) == [OPERATIONAL_FORWARD_OOS_START_UTC]
    assert context["data_quality"]["forward_complete_hour_rows"] == 1
    assert context["data_quality"]["expected_complete_hours_through_latest_complete"] == 1
    assert context["data_quality"]["missing_complete_hours"] == 0


def test_frozen_common_strategy_runner_executes_existing_implementations() -> None:
    hourly, funding = _engine_hourly()
    results = _common_results(hourly, funding)
    assert set(results) == {
        "funding_extreme_reversal:v1",
        "oi_momentum:v1",
        "volatility_adjusted_trend_breakout:v1",
        "volatility_compression_breakout:v1",
        "funding_aligned_momentum:v1",
    }
    for result in results.values():
        assert result.strategy_version == "v1"
        assert not result.signals.empty


def test_regime_detector_is_multilabel() -> None:
    regime = RegimeDetectorV1().detect(_hourly(0.0002))
    assert regime.trend == "BULL"
    assert regime.funding == "HIGH_POSITIVE_FUNDING"
    assert "BULL" in regime.tags
    assert "HIGH_POSITIVE_FUNDING" in regime.tags


def test_regime_timestamp_uses_decision_timestamp_when_present() -> None:
    hourly = _hourly(0.0002)
    hourly["decision_timestamp"] = hourly["timestamp"] + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1)
    regime = RegimeDetectorV1().detect(hourly)
    assert regime.timestamp == hourly.iloc[-1]["decision_timestamp"]


def test_selector_only_allows_funding_carry_and_requires_risk_gate() -> None:
    registry = load_default_registry()
    selector = StrategySelectorFoundation(registry)
    regime = RegimeDetectorV1().detect(_hourly(0.0002))
    assert not any(row["eligible"] for row in selector.evaluate(regime, risk_gate_passed=False))
    allowed = selector.evaluate(regime, risk_gate_passed=True)
    eligible = [row for row in allowed if row["eligible"]]
    assert [(row["strategy_name"], row["strategy_version"]) for row in eligible] == [("funding_carry", "v1")]
    assert all(row["hypothetical_only"] for row in allowed)
    assert all(not row["paper_order"] and not row["live_order"] for row in allowed)


def test_pair_overlap_uses_perpetual_short_leg_for_direction() -> None:
    ts = pd.Timestamp("2026-07-22T00:00:00Z")
    signals = pd.DataFrame([
        {"timestamp": ts, "strategy_name": "funding_carry", "signal": "PAIR_LONG_SPOT_SHORT_PERP"},
        {"timestamp": ts, "strategy_name": "funding_extreme_short", "signal": "SHORT"},
        {"timestamp": ts, "strategy_name": "other_long", "signal": "LONG"},
    ])
    overlap = SignalOverlapAnalyzer.summarize(signals)
    carry_short = overlap[(overlap["strategy_a"] == "funding_carry") & (overlap["strategy_b"] == "funding_extreme_short")].iloc[0]
    assert carry_short["same_direction_rate"] == 1.0
    carry_long = overlap[(overlap["strategy_a"] == "funding_carry") & (overlap["strategy_b"] == "other_long")].iloc[0]
    assert carry_long["opposite_direction_rate"] == 1.0


def test_conflict_manager_detects_duplicate_and_opposite_perpetual_exposure() -> None:
    result = ConflictManager.analyze([
        PositionRequest("funding_carry", "v1", spot_btc=1.0, perpetual_btc=-1.0, margin_usage=0.5),
        PositionRequest("funding_extreme_reversal", "v1", perpetual_btc=-0.5, margin_usage=0.2),
        PositionRequest("other", "v1", perpetual_btc=0.25, margin_usage=0.1),
    ])
    assert result["net_btc_delta"] == pytest.approx(-0.25)
    assert result["gross_exposure_btc"] == pytest.approx(2.75)
    assert result["same_direction_duplicates"]
    assert result["opposite_direction_conflicts"]
    assert result["has_conflict"] is True
