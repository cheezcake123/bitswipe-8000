from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from strategy_arena.strategies import BaseStrategy, Signal, SignalType


@dataclass
class VolatilityCompressionBreakoutV1(BaseStrategy):
    """Fixed ATR-compression breakout hypothesis selected before Final OOS.

    Default v1 is SHORT-only because the pre-strategy relationship study showed the
    recent 2024-2026 short breakout relationship was materially more persistent than LONG.
    LONG_ONLY and BOTH modes exist only for symmetric comparison with identical rules.
    """

    strategy_name: str = "volatility_compression_breakout"
    strategy_version: str = "v1"
    direction_mode: str = "SHORT_ONLY"  # SHORT_ONLY is the frozen v1 candidate.
    compression_percentile: float = 0.20
    percentile_lookback_hours: int = 90 * 24
    atr_window_hours: int = 24
    range_window_hours: int = 24
    max_holding_hours: int = 24
    max_leverage: float = 2.0
    uses_precomputed_features: bool = True

    _last_position: int = field(default=0, init=False, repr=False)
    _pending_signal_time: pd.Timestamp | None = field(default=None, init=False, repr=False)
    _pending_boundary: float | None = field(default=None, init=False, repr=False)
    _pending_side: int = field(default=0, init=False, repr=False)
    _entry_estimate: pd.Timestamp | None = field(default=None, init=False, repr=False)
    _active_boundary: float | None = field(default=None, init=False, repr=False)

    def _observe_position_transition(self, current_position: int, ts: pd.Timestamp) -> None:
        if current_position != 0 and self._last_position == 0:
            self._entry_estimate = (
                self._pending_signal_time + pd.Timedelta(hours=1)
                if self._pending_signal_time is not None
                else ts
            )
            self._active_boundary = self._pending_boundary
        elif current_position == 0 and self._last_position != 0:
            self._entry_estimate = None
            self._active_boundary = None
            self._pending_signal_time = None
            self._pending_boundary = None
            self._pending_side = 0
        self._last_position = current_position

    def generate_signal(self, history: pd.DataFrame, symbol: str, current_position: int) -> Signal:
        row = history.iloc[-1]
        decision_ts = pd.Timestamp(row.get("decision_timestamp", row.get("close_time", row["timestamp"])))
        row_ts = pd.Timestamp(row["timestamp"])
        ts = decision_ts.isoformat()
        self._observe_position_transition(current_position, row_ts)

        ready = bool(row.get("vc_ready", False))
        meta = {
            "compression_method": "ATR_PERCENT",
            "compression_percentile": self.compression_percentile,
            "percentile_lookback_hours": self.percentile_lookback_hours,
            "atr_window_hours": self.atr_window_hours,
            "range_window_hours": self.range_window_hours,
            "max_holding_hours": self.max_holding_hours,
            "direction_mode": self.direction_mode,
            "atr_pct": float(row["vc_atr_pct"]) if pd.notna(row.get("vc_atr_pct")) else None,
            "compression_threshold": float(row["vc_compression_threshold"]) if pd.notna(row.get("vc_compression_threshold")) else None,
            "range_upper": float(row["vc_range_upper"]) if pd.notna(row.get("vc_range_upper")) else None,
            "range_lower": float(row["vc_range_lower"]) if pd.notna(row.get("vc_range_lower")) else None,
            "precomputed_from_past_only": True,
        }
        if not ready:
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.HOLD, metadata={**meta, "reason": "warmup"})

        close = float(row["close"])
        if current_position != 0:
            if self._entry_estimate is not None:
                elapsed_signal_bars = (row_ts - self._entry_estimate) / pd.Timedelta(hours=1)
                # Signal at the close of the 24th held bar; common engine exits next bar open.
                if elapsed_signal_bars >= self.max_holding_hours - 1:
                    return Signal(
                        self.strategy_name,
                        self.strategy_version,
                        ts,
                        symbol,
                        SignalType.EXIT,
                        0.7,
                        exit_reason=f"Maximum holding time reached ({self.max_holding_hours}h)",
                        metadata=meta,
                    )
            if current_position > 0 and self._active_boundary is not None and close <= self._active_boundary:
                return Signal(
                    self.strategy_name,
                    self.strategy_version,
                    ts,
                    symbol,
                    SignalType.EXIT,
                    0.7,
                    exit_reason="LONG breakout failed: close re-entered original range",
                    metadata=meta,
                )
            if current_position < 0 and self._active_boundary is not None and close >= self._active_boundary:
                return Signal(
                    self.strategy_name,
                    self.strategy_version,
                    ts,
                    symbol,
                    SignalType.EXIT,
                    0.7,
                    exit_reason="SHORT breakout failed: close re-entered original range",
                    metadata=meta,
                )
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.HOLD, metadata=meta)

        allow_long = self.direction_mode in {"LONG_ONLY", "BOTH"}
        allow_short = self.direction_mode in {"SHORT_ONLY", "BOTH"}
        if allow_long and bool(row.get("vc_entry_long", False)):
            self._pending_signal_time = row_ts
            self._pending_boundary = float(row["vc_range_upper"])
            self._pending_side = 1
            return Signal(
                self.strategy_name,
                self.strategy_version,
                ts,
                symbol,
                SignalType.LONG,
                0.6,
                entry_reason="ATR% compression then close above prior 24h range",
                metadata=meta,
            )
        if allow_short and bool(row.get("vc_entry_short", False)):
            self._pending_signal_time = row_ts
            self._pending_boundary = float(row["vc_range_lower"])
            self._pending_side = -1
            return Signal(
                self.strategy_name,
                self.strategy_version,
                ts,
                symbol,
                SignalType.SHORT,
                0.6,
                entry_reason="ATR% compression then close below prior 24h range",
                metadata=meta,
            )
        return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.HOLD, metadata=meta)
