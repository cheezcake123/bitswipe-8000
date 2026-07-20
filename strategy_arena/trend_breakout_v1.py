from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from strategy_arena.strategies import BaseStrategy, Signal, SignalType


@dataclass
class VolatilityAdjustedTrendBreakoutV1(BaseStrategy):
    """55-day Donchian entry / 20-day exit with backward-looking volatility sizing.

    The strategy runs on the common 1h backtest stream but only decides at the final
    hourly candle of each UTC day. Signals therefore execute at the next 1h bar open,
    which is the next UTC day's open. No AI is used.
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
    bars_per_day: int = 24
    uses_precomputed_features: bool = True

    @property
    def max_lookback_bars(self) -> int:
        required_days = max(
            self.entry_window_days + 1,
            self.exit_window_days + 1,
            self.atr_window_days + self.volatility_reference_min_days + 1,
        )
        return required_days * self.bars_per_day

    @staticmethod
    def _decision_timestamp(row: pd.Series) -> pd.Timestamp:
        return pd.Timestamp(row.get("decision_timestamp", row.get("close_time", row["timestamp"])))

    def _daily_history(self, history: pd.DataFrame) -> pd.DataFrame:
        hourly = history[["timestamp", "open", "high", "low", "close", "volume"]].copy()
        hourly["timestamp"] = pd.to_datetime(hourly["timestamp"], utc=True)
        return (
            hourly.set_index("timestamp")
            .resample("1D")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .dropna()
            .reset_index()
        )

    def _atr_percent_series(self, daily: pd.DataFrame) -> pd.Series:
        prev_close = daily["close"].shift(1)
        tr = pd.concat(
            [
                daily["high"] - daily["low"],
                (daily["high"] - prev_close).abs(),
                (daily["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = tr.rolling(self.atr_window_days, min_periods=self.atr_window_days).mean()
        return atr / daily["close"]

    def _position_size_hint(self, daily: pd.DataFrame) -> tuple[float | None, dict]:
        atr_pct = self._atr_percent_series(daily)
        current_atr_pct = float(atr_pct.iloc[-1]) if len(atr_pct) and pd.notna(atr_pct.iloc[-1]) else None
        past = atr_pct.iloc[:-1].dropna().tail(self.volatility_reference_days)
        if current_atr_pct is None or current_atr_pct <= 0 or len(past) < self.volatility_reference_min_days:
            return None, {"atr_pct": current_atr_pct, "volatility_reference": None}
        reference = float(past.median())
        if reference <= 0:
            return None, {"atr_pct": current_atr_pct, "volatility_reference": reference}
        size = min(self.max_position_fraction, reference / current_atr_pct)
        return max(0.0, float(size)), {"atr_pct": current_atr_pct, "volatility_reference": reference}

    def _from_precomputed(self, row: pd.Series, symbol: str, current_position: int, ts: str) -> Signal | None:
        if "trend_ready" not in row.index:
            return None
        if not bool(row.get("trend_ready", False)):
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.HOLD, metadata={"reason": "warmup"})
        raw_size = row.get("trend_position_size_hint")
        size_hint = float(raw_size) if pd.notna(raw_size) else None
        metadata = {
            "entry_upper_55d": float(row["trend_entry_upper"]),
            "entry_lower_55d": float(row["trend_entry_lower"]),
            "exit_upper_20d": float(row["trend_exit_upper"]),
            "exit_lower_20d": float(row["trend_exit_lower"]),
            "atr_pct": float(row["trend_atr_pct"]),
            "volatility_reference": float(row["trend_volatility_reference"]),
            "position_size_hint": size_hint,
            "params": self.__dict__.copy(),
            "precomputed_from_past_only": True,
        }
        if current_position > 0 and bool(row["trend_exit_long"]):
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.EXIT, 0.7, position_size_hint=size_hint, exit_reason="Long trend ended: close below prior 20-day low", metadata=metadata)
        if current_position < 0 and bool(row["trend_exit_short"]):
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.EXIT, 0.7, position_size_hint=size_hint, exit_reason="Short trend ended: close above prior 20-day high", metadata=metadata)
        if current_position == 0 and bool(row["trend_entry_long"]):
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.LONG, 0.65, position_size_hint=size_hint, entry_reason="Close broke above prior 55-day Donchian high", metadata=metadata)
        if current_position == 0 and bool(row["trend_entry_short"]):
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.SHORT, 0.65, position_size_hint=size_hint, entry_reason="Close broke below prior 55-day Donchian low", metadata=metadata)
        return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.HOLD, position_size_hint=size_hint, metadata=metadata)

    def generate_signal(self, history: pd.DataFrame, symbol: str, current_position: int) -> Signal:
        row = history.iloc[-1]
        decision_ts = self._decision_timestamp(row)
        ts = decision_ts.isoformat()
        if pd.Timestamp(row["timestamp"]).hour != 23:
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.HOLD)

        precomputed = self._from_precomputed(row, symbol, current_position, ts)
        if precomputed is not None:
            return precomputed

        if len(history) < self.max_lookback_bars:
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.HOLD, metadata={"reason": "warmup", "params": self.__dict__.copy()})
        daily = self._daily_history(history)
        if len(daily) < max(self.entry_window_days + 1, self.atr_window_days + self.volatility_reference_min_days + 1):
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.HOLD, metadata={"reason": "daily_warmup"})

        prior_entry = daily.iloc[-1 - self.entry_window_days : -1]
        prior_exit = daily.iloc[-1 - self.exit_window_days : -1]
        entry_upper = float(prior_entry["high"].max())
        entry_lower = float(prior_entry["low"].min())
        exit_upper = float(prior_exit["high"].max())
        exit_lower = float(prior_exit["low"].min())
        close = float(daily.iloc[-1]["close"])
        size_hint, vol_meta = self._position_size_hint(daily)
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
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.EXIT, 0.7, position_size_hint=size_hint, exit_reason="Long trend ended: close below prior 20-day low", metadata=metadata)
        if current_position < 0 and close > exit_upper:
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.EXIT, 0.7, position_size_hint=size_hint, exit_reason="Short trend ended: close above prior 20-day high", metadata=metadata)
        if current_position == 0 and close > entry_upper:
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.LONG, 0.65, position_size_hint=size_hint, entry_reason="Close broke above prior 55-day Donchian high", metadata=metadata)
        if current_position == 0 and close < entry_lower:
            return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.SHORT, 0.65, position_size_hint=size_hint, entry_reason="Close broke below prior 55-day Donchian low", metadata=metadata)
        return Signal(self.strategy_name, self.strategy_version, ts, symbol, SignalType.HOLD, position_size_hint=size_hint, metadata=metadata)
