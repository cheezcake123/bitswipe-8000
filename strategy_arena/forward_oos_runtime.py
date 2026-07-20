from __future__ import annotations

import importlib
import json
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
    "funding_carry:v1": {"mode": "existing_two_leg_simulator", "implementation": "strategy_arena.funding_carry_v1:FundingCarryV1Config"},
    "funding_extreme_reversal:v1": {"mode": "common_signal_class", "implementation": "strategy_arena.strategies:FundingExtremeReversalV1"},
    "basis_mean_reversion:v1": {"mode": "existing_basis_research_execution", "implementation": "strategy_arena.basis_research:BasisResearchConfig + strategy_arena.basis_execution"},
    "volatility_adjusted_trend_breakout:v1": {"mode": "common_signal_class", "implementation": "strategy_arena.trend_breakout_v1:VolatilityAdjustedTrendBreakoutV1"},
    "volatility_compression_breakout:v1": {"mode": "common_signal_class_with_existing_precomputed_features", "implementation": "strategy_arena.volatility_compression_v1:VolatilityCompressionBreakoutV1"},
    "funding_aligned_momentum:v1": {"mode": "common_signal_class_with_existing_precomputed_features", "implementation": "strategy_arena.funding_aligned_momentum_v1:FundingAlignedMomentumV1"},
    "oi_momentum:v1": {"mode": "common_signal_class", "implementation": "strategy_arena.strategies:OIMomentumV1"},
}


def _load_symbol(ref: str) -> Any:
    module_name, symbol_name = ref.split(":", 1)
    return getattr(importlib.import_module(module_name), symbol_name)


def validate_frozen_runtime_parameters(registry: FrozenStrategyRegistry) -> dict:
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
            if hasattr(obj, name) and getattr(obj, name) != expected:
                mismatches[name] = {"expected": expected, "actual": getattr(obj, name)}
        checks[key] = {"pass": not mismatches, "mismatches": mismatches, "implementation": ref}

    carry_record = registry.records["funding_carry:v1"]
    carry = FundingCarryV1Config()
    carry_mismatches = {}
    for name, expected in carry_record.parameters.items():
        if hasattr(carry, name) and getattr(carry, name) != expected:
            carry_mismatches[name] = {"expected": expected, "actual": getattr(carry, name)}
    checks["funding_carry:v1"] = {"pass": not carry_mismatches, "mismatches": carry_mismatches, "implementation": carry_record.implementation_ref}

    basis = registry.records["basis_mean_reversion:v1"]
    checks["basis_mean_reversion:v1"] = {
        "pass": "basis_research" in basis.implementation_ref and "basis_execution" in basis.implementation_ref,
        "mismatches": {},
        "implementation": basis.implementation_ref,
    }
    return {"pass": all(item["pass"] for item in checks.values()), "strategies": checks}


class ForwardOOSSignalRecorder:
    """Persist outputs from frozen implementations without interpreting or overriding them."""

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
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
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
            "metadata_json": json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True, default=str),
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


def registry_snapshot(registry: FrozenStrategyRegistry) -> list[dict]:
    rows = []
    for record in registry.records.values():
        row = asdict(record)
        row["strategy_key"] = record.key
        row["fingerprint"] = record.fingerprint
        row["adapter"] = ADAPTER_CATALOG[record.key]
        rows.append(row)
    return rows
