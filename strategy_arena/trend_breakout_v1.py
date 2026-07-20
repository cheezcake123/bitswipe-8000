from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from strategy_arena.strategies import BaseStrategy, Signal, SignalType


@dataclass
class VolatilityAdjustedTrendBreakoutV1(BaseStrategy):
    """Deterministic 55-day Donchian entry / 20-day exit with backward-looking ATR sizing.

    v1 parameters are intentionally fixed after the 20d-vs-55d relationship study.
    No AI or parameter optimization is involved during signal generation.
    """

    strategy_name: str = "volatility_adjusted_trend_breakout"
    strategy_version: str = "v1"
    entry_window_days: int = 55
    exit_window_days: int = 20
    atr_window_days: int = 14
    volatility_reference_days: int = 252
    volatility_reference_min_days: int = 126
    max_position_fraction: float = 1.0
    max_leverage: float = 2.0

    @property
    def max_lookback_bars(self) -> int:
        return max(
            self.entry_window_days + 1,
            self.exit_window_days + 1,
            self.atr_window_days + self.volatility_reference_min_days + 1,
        )

    def _atr_percent_series(self, history: pd.DataFrame) -> pd.Series:
        prev_close = history["close"].shift(1)
        tr = pd.concat(
            [
                history["high"] - history["low"],
                (history["high"] - prev_close).abs(),
                (history["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = tr.rolling(self.atr_window_days, min_periods=self.atr_window_days).mean()
        return atr / history["close"]

    def _position_size_hint(self, history: pd.DataFrame) -> tuple[float | None, dict]:
        atr_pct = self._atr_percent_series(history)
        current_atr_pct = float(atr_pct.iloc[-1]) if pd.notna(atr_pct.iloc[-1]) else None
        past = atr_pct.iloc[:-1].dropna().tail(self.volatility_reference_days)
        if current_atr_pct is None or current_atr_pct <= 0 or len(past) < self.volatility_reference_min_days:
            return None, {"atr_pct": current_atr_pct, "volatility_reference": None}
        reference = float(past.median())
        if reference <= 0:
            return None, {"atr_pct": current_atr_pct, "volatility_reference": reference}
        size = min(self.max_position_fraction, reference / current_atr_pct)
        size = max(0.0, float(size))
        return size, {"atr_pct": current_atr_pct, "volatility_reference": reference}

    def generate_signal(self, history: pd.DataFrame, symbol: str, current_position: int) -> Signal:
        row = history.iloc[-1]
        decision_ts = row.get("decision_timestamp", row.get("close_time", row["timestamp"]))
        ts = pd.Timestamp(decision_ts).isoformat()
        if len(history) < self.max_lookback_bars:
            return Signal(
                self.strategy_name,
                self.strategy_version,
                ts,
                symbol,
                SignalType.HOLD,
                metadata={"reason": "warmup", "params": self.__dict__.copy()},
            )

        prior_entry = history.iloc[-1 - self.entry_window_days : -1]
        prior_exit = history.iloc[-1 - self.exit_window_days : -1]
        entry_upper = float(prior_entry["high"].max())
        entry_lower = float(prior_entry["low"].min())
        exit_upper = float(prior_exit["high"].max())
        exit_lower = float(prior_exit["low"].min())
        close = float(row["close"])
        size_hint, vol_meta = self._position_size_hint(history)

        metadata = {
            "entry_upper_55d": entry_upper,
            "entry_lower_55d": entry_lower,
            "exit_upper_20d": exit_upper,
            "exit_lower_20d": exit_lower,
            "position_size_hint": size_hint,
            "params": self.__dict__.copy(),
            **vol_meta,
        }

        if current_position > 0 and close < exit_lower:
            return Signal(
                self.strategy_name,
                self.strategy_version,
                ts,
                symbol,
                SignalType.EXIT,
                0.7,
                position_size_hint=size_hint,
                exit_reason="Long trend ended: close below prior 20-day low",
                metadata=metadata,
            )
        if current_position < 0 and close > exit_upper:
            return Signal(
                self.strategy_name,
                self.strategy_version,
                ts,
                symbol,
                SignalType.EXIT,
                0.7,
                position_size_hint=size_hint,
                exit_reason="Short trend ended: close above prior 20-day high",
                metadata=metadata,
            )
        if current_position == 0 and close > entry_upper:
            return Signal(
                self.strategy_name,
                self.strategy_version,
                ts,
                symbol,
                SignalType.LONG,
                0.65,
                position_size_hint=size_hint,
                entry_reason="Close broke above prior 55-day Donchian high",
                metadata=metadata,
            )
        if current_position == 0 and close < entry_lower:
            return Signal(
                self.strategy_name,
                self.strategy_version,
                ts,
                symbol,
                SignalType.SHORT,
                0.65,
                position_size_hint=size_hint,
                entry_reason="Close broke below prior 55-day Donchian low",
                metadata=metadata,
            )
        return Signal(
            self.strategy_name,
            self.strategy_version,
            ts,
            symbol,
            SignalType.HOLD,
            position_size_hint=size_hint,
            metadata=metadata,
        )
