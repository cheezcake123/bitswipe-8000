from __future__ import annotations

import importlib
from dataclasses import asdict
from typing import Any

import pandas as pd

from strategy_arena.forward_oos_selector_foundation import (
    FORWARD_OOS_START_UTC,
    AppendOnlyLeagueStore,
    FrozenStrategyRegistry,
    RegimeSnapshot,
)
from strategy_arena.funding_carry_v1 import FundingCarryV1Config


ADAPTER_CATALOG = {
    "funding_carry:v1": {
        "mode": "existing_two_leg_simulator",
        "implementation": "strategy_arena.funding_carry_v1:FundingCarryV1Config",
        "signal_semantics": "PAIR_LONG_SPOT_SHORT_PERP when frozen +2bp observed-funding condition is met",
    },
    "funding_extreme_reversal:v1": {
        "mode": "common_signal_class",
        "implementation": "strategy_arena.strategies:FundingExtremeReversalV1",
    },
    "basis_mean_reversion:v1": {
        "mode": "existing_basis_research_execution",
        "implementation": "strategy_arena.basis_research:BasisResearchConfig + strategy_arena.basis_execution",
    },
    "volatility_adjusted_trend_breakout:v1": {
        "mode": "common_signal_class",
        "implementation": "strategy_arena.trend_breakout_v1:VolatilityAdjustedTrendBreakoutV1",
    },
    "volatility_compression_breakout:v1": {
        "mode": "common_signal_class_with_existing_precomputed_features",
        "implementation": "strategy_arena.volatility_compression_v1:VolatilityCompressionBreakoutV1",
    },
    "funding_aligned_momentum:v1": {
        "mode": "common_signal_class_with_existing_precomputed_features",
        "implementation": "strategy_arena.funding_aligned_momentum_v1:FundingAlignedMomentumV1",
    },
    "oi_momentum:v1": {
        "mode": "common_signal_class",
        "implementation": "strategy_arena.strategies:OIMomentumV1",
    },
}


def _load_symbol(ref: str) -> Any:
    module_name, symbol_name = ref.split(":", 1)
    module = importlib.import_module(module_name)
    return getattr(module, symbol_name)


def validate_frozen_runtime_parameters(registry: FrozenStrategyRegistry) -> dict:
    """Verify runtime dataclass defaults still match the frozen registry.

    Basis Mean Reversion uses the existing research/execution pipeline rather than a single
    strategy dataclass, so its registry entry is checked structurally by implementation_ref.
    No strategy source is modified here.
    """
    checks: dict[str, dict] = {}
    runtime_refs = {
        "funding_extreme_reversal:v1": "strategy_arena.strategies:FundingExtremeReversalV1",
        "volatility_adjusted_trend_breakout:v1": "strategy_arena.trend_breakout_v1:VolatilityAdjustedTrendBreakoutV1",
        "volatility_compression_breakout:v1": "strategy_arena.volatility_compression_v1:VolatilityCompressionBreakoutV1",
        "funding_aligned_momentum:v1": "strategy_arena.funding_aligned_momentum_v1:FundingAlignedMomentumV1",
        "oi_momentum:v1": "strategy_arena.strategies:OIMomentumV1",
    }
    for key, ref in runtime_refs.items():
        record = registry.records[key]
        obj = _load_symbol(ref)()
        mismatches = {}
        for name, expected in record.parameters.items():
            if not hasattr(obj, name):
                continue
            actual = getattr(obj, name)
            if actual != expected:
                mismatches[name] = {"expected": expected, "actual": actual}
        checks[key] = {"pass": not mismatches, "mismatches": mismatches, "implementation": ref}

    carry_record = registry.records["funding_carry:v1"]
    carry = FundingCarryV1Config()
    carry_mismatches = {}
    for name, expected in carry_record.parameters.items():
        if hasattr(carry, name) and getattr(carry, name) != expected:
            carry_mismatches[name] = {"expected": expected, "actual": getattr(carry, name)}
    checks["funding_carry:v1"] = {
        "pass": not carry_mismatches,
        "mismatches": carry_mismatches,
        "implementation": "strategy_arena.funding_carry_v1:FundingCarryV1Config",
    }

    basis = registry.records["basis_mean_reversion:v1"]
    checks["basis_mean_reversion:v1"] = {
        "pass": "basis_research" in basis.implementation_ref and "basis_execution" in basis.implementation_ref,
        "mismatches": {},
        "implementation": basis.implementation_ref,
    }
    return {"pass": all(item["pass"] for item in checks.values()), "strategies": checks}


class ForwardOOSSignalRecorder:
    """Append frozen-strategy outputs without changing or selecting their signals."""

    def __init__(self, registry: FrozenStrategyRegistry, store: AppendOnlyLeagueStore):
        self.registry = registry
        self.store = store

    def record_signal(
        self,
        *,
        strategy_key: str,
        timestamp,
        signal: str,
        confidence: float | None,
        entry_reason: str | None,
        exit_reason: str | None,
        regime: RegimeSnapshot,
        funding: float | None,
        open_interest: float | None,
        basis_bps: float | None,
        volatility: float | None,
        metadata: dict | None = None,
    ):
        if strategy_key not in self.registry.records:
            raise KeyError(f"Unknown frozen strategy: {strategy_key}")
        ts = pd.Timestamp(timestamp)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        if ts < FORWARD_OOS_START_UTC:
            raise ValueError("Cannot record pre-Forward-OOS signal")
        record = self.registry.records[strategy_key]
        return self.store.append("signals", [{
            "timestamp": ts,
            "strategy_name": record.strategy_name,
            "strategy_version": record.strategy_version,
            "strategy_fingerprint": record.fingerprint,
            "status": record.status,
            "verdict": record.verdict,
            "allowed_for_selector": record.allowed_for_selector,
            "allowed_for_paper": record.allowed_for_paper,
            "signal": str(signal),
            "confidence": confidence,
            "entry_reason": entry_reason,
            "exit_reason": exit_reason,
            "market_regime": "|".join(regime.tags),
            "funding": funding,
            "open_interest": open_interest,
            "basis_bps": basis_bps,
            "volatility": volatility,
            "shadow_only": not record.allowed_for_selector,
            "metadata_json": json_dumps(metadata or {}),
            "actual_order_created": False,
            "paper_order_created": False,
        }])

    def record_hypothetical_fill(
        self,
        *,
        strategy_key: str,
        timestamp,
        leg: str,
        side: str,
        quantity_btc: float,
        reference_price: float,
        hypothetical_fill_price: float,
        fee_cost: float = 0.0,
        slippage_cost: float = 0.0,
        funding_cashflow: float = 0.0,
    ):
        record = self.registry.records[strategy_key]
        return self.store.append("hypothetical_fills", [{
            "timestamp": pd.Timestamp(timestamp),
            "strategy_name": record.strategy_name,
            "strategy_version": record.strategy_version,
            "strategy_fingerprint": record.fingerprint,
            "leg": leg,
            "side": side,
            "quantity_btc": quantity_btc,
            "reference_price": reference_price,
            "hypothetical_fill_price": hypothetical_fill_price,
            "fee_cost": fee_cost,
            "slippage_cost": slippage_cost,
            "funding_cashflow": funding_cashflow,
            "hypothetical_only": True,
            "actual_order_created": False,
            "paper_order_created": False,
        }])


def json_dumps(value: dict) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def registry_snapshot(registry: FrozenStrategyRegistry) -> list[dict]:
    rows = []
    for record in registry.records.values():
        row = asdict(record)
        row["strategy_key"] = record.key
        row["fingerprint"] = record.fingerprint
        row["adapter"] = ADAPTER_CATALOG[record.key]
        rows.append(row)
    return rows
