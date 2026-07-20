from __future__ import annotations

import json

import pandas as pd
import pytest

from strategy_arena.forward_oos_runtime import validate_frozen_runtime_parameters
from strategy_arena.forward_oos_selector_foundation import (
    FORWARD_OOS_START_UTC,
    AppendOnlyLeagueStore,
    ConflictManager,
    PositionRequest,
    RegimeDetectorV1,
    StrategySelectorFoundation,
    load_default_registry,
)


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


def test_registry_policy_and_frozen_runtime_parameters() -> None:
    registry = load_default_registry()
    selector = [r.key for r in registry.selector_eligible()]
    assert selector == ["funding_carry:v1"]
    assert all(not r.allowed_for_paper for r in registry.records.values())
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
    runtime = validate_frozen_runtime_parameters(registry)
    assert runtime["pass"] is True


def test_forward_oos_start_is_fixed_and_historical_rows_are_rejected(tmp_path) -> None:
    store = AppendOnlyLeagueStore(tmp_path / "league")
    metadata = json.loads((tmp_path / "league" / "league_metadata.json").read_text())
    assert pd.Timestamp(metadata["forward_oos_start_utc"]) == FORWARD_OOS_START_UTC
    with pytest.raises(ValueError):
        store.append("signals", [{"timestamp": "2026-06-30T23:00:00Z", "strategy_name": "x", "signal": "HOLD"}])


def test_regime_detector_is_backward_only_and_multilabel() -> None:
    regime = RegimeDetectorV1().detect(_hourly(0.0002))
    assert regime.trend == "BULL"
    assert regime.funding == "HIGH_POSITIVE_FUNDING"
    assert "BULL" in regime.tags
    assert "HIGH_POSITIVE_FUNDING" in regime.tags


def test_selector_only_allows_funding_carry_and_requires_risk_gate() -> None:
    registry = load_default_registry()
    selector = StrategySelectorFoundation(registry)
    regime = RegimeDetectorV1().detect(_hourly(0.0002))
    blocked = selector.evaluate(regime, risk_gate_passed=False)
    assert not any(row["eligible"] for row in blocked)
    allowed = selector.evaluate(regime, risk_gate_passed=True)
    eligible = [row for row in allowed if row["eligible"]]
    assert [(row["strategy_name"], row["strategy_version"]) for row in eligible] == [("funding_carry", "v1")]
    assert all(row["hypothetical_only"] for row in allowed)
    assert all(not row["paper_order"] and not row["live_order"] for row in allowed)


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
