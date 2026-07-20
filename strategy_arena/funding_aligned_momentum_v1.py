from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from strategy_arena.strategies import BaseStrategy, Signal, SignalType


@dataclass
class FundingAlignedMomentumV1(BaseStrategy):
    """Research-only funding-aligned momentum strategy.

    Frozen v1 after the relationship study:
    - 7-day (168h) price momentum.
    - LONG-only by default: positive momentum + latest actually observed funding < 0.
    - Exit when either positive momentum or negative funding condition disappears.
    - Signal is decided at candle close; common engine executes at next candle open.
    """

    strategy_name: str = "funding_aligned_momentum"
    strategy_version: str = "v1"
    momentum_hours: int = 168
    direction_mode: str = "LONG_ONLY"  # LONG_ONLY, SHORT_ONLY, BOTH; v1 is frozen LONG_ONLY.
    max_leverage: float = 2.0
    uses_precomputed_features: bool = True

    @property
    def max_lookback_bars(self) -> int:
        return self.momentum_hours + 1

    def generate_signal(self, history: pd.DataFrame, symbol: str, current_position: int) -> Signal:
        row = history.iloc[-1]
        ts = pd.Timestamp(row.get("decision_timestamp", row["timestamp"])).isoformat()
        ready = bool(row.get("fam_ready", False))
        if not ready:
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.HOLD, metadata={"reason": "warmup"})

        momentum = float(row["fam_momentum_return"])
        known_funding = float(row["fam_known_funding_rate"])
        long_condition = momentum > 0 and known_funding < 0
        short_condition = momentum < 0 and known_funding > 0
        metadata = {
            "momentum_hours": self.momentum_hours,
            "momentum_return": momentum,
            "known_funding_rate": known_funding,
            "known_funding_timestamp": str(row.get("fam_known_funding_timestamp", "")),
            "direction_mode": self.direction_mode,
            "funding_rule": "LONG requires funding<0; SHORT requires funding>0; sign-only, no optimized magnitude threshold",
            "precomputed_from_past_only": True,
        }

        if current_position > 0 and not long_condition:
            return Signal(
                self.strategy_name, self.strategy_version, ts, symbol, SignalType.EXIT, 0.7,
                exit_reason="Positive 7-day momentum or negative known funding condition disappeared",
                metadata=metadata,
            )
        if current_position < 0 and not short_condition:
            return Signal(
                self.strategy_name, self.strategy_version, ts, symbol, SignalType.EXIT, 0.7,
                exit_reason="Negative 7-day momentum or positive known funding condition disappeared",
                metadata=metadata,
            )
        if current_position == 0:
            if self.direction_mode in {"LONG_ONLY", "BOTH"} and long_condition:
                return Signal(
                    self.strategy_name, self.strategy_version, ts, symbol, SignalType.LONG, 0.65,
                    entry_reason="Positive 7-day momentum aligned with negative known funding",
                    metadata=metadata,
                )
            if self.direction_mode in {"SHORT_ONLY", "BOTH"} and short_condition:
                return Signal(
                    self.strategy_name, self.strategy_version, ts, symbol, SignalType.SHORT, 0.65,
                    entry_reason="Negative 7-day momentum aligned with positive known funding",
                    metadata=metadata,
                )
        return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.HOLD, metadata=metadata)
