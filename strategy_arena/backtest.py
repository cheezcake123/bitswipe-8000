from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from strategy_arena.strategies import BaseStrategy, SignalType

HOURS_PER_YEAR = 365 * 24


@dataclass(frozen=True)
class TimeSplit:
    name: str
    start: pd.Timestamp
    end: pd.Timestamp


def chronological_split(df: pd.DataFrame, train: float = 0.6, validation: float = 0.2) -> list[TimeSplit]:
    if not 0 < train < 1 or not 0 < validation < 1 or train + validation >= 1:
        raise ValueError("invalid split fractions")
    n = len(df)
    i1, i2 = max(1, int(n * train)), max(2, int(n * (train + validation)))
    return [
        TimeSplit("train", df.iloc[0]["timestamp"], df.iloc[i1 - 1]["timestamp"]),
        TimeSplit("validation", df.iloc[i1]["timestamp"], df.iloc[i2 - 1]["timestamp"]),
        TimeSplit("oos", df.iloc[i2]["timestamp"], df.iloc[-1]["timestamp"]),
    ]


def calculate_metrics(
    equity: pd.DataFrame,
    trades: pd.DataFrame,
    initial_equity: float,
    periods_per_year: float = HOURS_PER_YEAR,
) -> dict:
    if equity.empty:
        return {}
    end_equity = float(equity.iloc[-1]["equity"])
    total_return = end_equity / initial_equity - 1.0
    periods = max(len(equity) - 1, 1)
    annualized_return = (1 + total_return) ** (periods_per_year / periods) - 1 if total_return > -1 else -1.0
    returns = equity["equity"].pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    std = float(returns.std(ddof=0)) if len(returns) else 0.0
    sharpe = float(returns.mean() / std * math.sqrt(periods_per_year)) if std > 0 else 0.0
    downside = returns[returns < 0]
    downside_std = float(downside.std(ddof=0)) if len(downside) else 0.0
    sortino = float(returns.mean() / downside_std * math.sqrt(periods_per_year)) if downside_std > 0 else 0.0
    peak = equity["equity"].cummax()
    drawdown = equity["equity"] / peak - 1.0
    if trades.empty:
        wins = losses = pd.Series(dtype=float)
        profit_factor = payoff = 0.0
        max_consecutive_losses = 0
    else:
        pnl = trades["net_pnl"]
        wins, losses = pnl[pnl > 0], pnl[pnl < 0]
        profit_factor = float(wins.sum() / abs(losses.sum())) if abs(losses.sum()) > 0 else (float("inf") if wins.sum() > 0 else 0.0)
        payoff = float(wins.mean() / abs(losses.mean())) if len(wins) and len(losses) else 0.0
        max_consecutive_losses = 0
        streak = 0
        for value in pnl:
            streak = streak + 1 if value < 0 else 0
            max_consecutive_losses = max(max_consecutive_losses, streak)
    turnover_notional = float(trades["turnover_notional"].sum()) if not trades.empty and "turnover_notional" in trades.columns else 0.0
    holding_hours = float(trades["holding_hours"].mean()) if not trades.empty and "holding_hours" in trades.columns else 0.0
    return {
        "total_return": total_return,
        "annualized_return": annualized_return,
        "maximum_drawdown": float(drawdown.min()),
        "sharpe": sharpe,
        "sortino": sortino,
        "profit_factor": profit_factor,
        "win_rate": float((trades["net_pnl"] > 0).mean()) if not trades.empty else 0.0,
        "average_win": float(wins.mean()) if len(wins) else 0.0,
        "average_loss": float(losses.mean()) if len(losses) else 0.0,
        "payoff_ratio": payoff,
        "number_of_trades": int(len(trades)),
        "maximum_consecutive_losses": int(max_consecutive_losses),
        "exposure": float((equity["position"] != 0).mean()),
        "turnover": turnover_notional / initial_equity,
        "fee_cost": float(trades["fee_cost"].sum()) if not trades.empty else 0.0,
        "slippage_cost": float(trades["slippage_cost"].sum()) if not trades.empty and "slippage_cost" in trades.columns else 0.0,
        "funding_cost": float(trades["funding_cost"].sum()) if not trades.empty else 0.0,
        "funding_income": float(trades["funding_income"].sum()) if not trades.empty else 0.0,
        "average_holding_hours": holding_hours,
    }


@dataclass(frozen=True)
class BacktestConfig:
    initial_equity: float = 10_000.0
    position_size: float = 1.0
    leverage: float = 2.0
    taker_fee_rate: float = 0.0005
    slippage_rate: float = 0.0005
    maintenance_margin_rate: float = 0.005
    periods_per_year: float = HOURS_PER_YEAR


@dataclass
class BacktestResult:
    strategy_name: str
    strategy_version: str
    metrics: dict
    equity_curve: pd.DataFrame
    trades: pd.DataFrame
    signals: pd.DataFrame


class BacktestEngine:
    def __init__(self, config: BacktestConfig | None = None):
        self.config = config or BacktestConfig()

    def run(self, data: pd.DataFrame, strategy: BaseStrategy, evaluation_start: pd.Timestamp | None = None, evaluation_end: pd.Timestamp | None = None) -> BacktestResult:
        cfg = self.config
        if cfg.leverage < 1:
            raise ValueError("leverage must be >= 1")
        strategy_max_leverage = getattr(strategy, "max_leverage", None)
        if strategy_max_leverage is not None and cfg.leverage > float(strategy_max_leverage):
            raise ValueError(f"configured leverage {cfg.leverage} exceeds strategy max_leverage {strategy_max_leverage}")
        start = pd.Timestamp(evaluation_start or data.iloc[0]["timestamp"])
        end = pd.Timestamp(evaluation_end or data.iloc[-1]["timestamp"])
        equity = cfg.initial_equity
        position = 0
        units = 0.0
        entry_price = 0.0
        entry_raw_price = 0.0
        entry_time = None
        entry_equity = 0.0
        current_trade_fee = 0.0
        current_slippage_cost = 0.0
        current_turnover_notional = 0.0
        current_funding_cost = 0.0
        current_funding_income = 0.0
        pending_signal = SignalType.HOLD
        pending_reason = ""
        pending_size_hint: float | None = None
        trades: list[dict] = []
        signals: list[dict] = []
        curve: list[dict] = []

        def close_position(ts, raw_price, reason, liquidation=False):
            nonlocal equity, position, units, entry_price, entry_raw_price, entry_time, entry_equity
            nonlocal current_trade_fee, current_slippage_cost, current_turnover_notional
            nonlocal current_funding_cost, current_funding_income
            if position == 0:
                return
            raw_price = float(raw_price)
            exit_price = raw_price * (1 - cfg.slippage_rate if position > 0 else 1 + cfg.slippage_rate)
            pre_cost_gross = position * units * (raw_price - entry_raw_price)
            after_slippage_gross = position * units * (exit_price - entry_price)
            exit_slippage = abs(units * (exit_price - raw_price))
            exit_notional = abs(units * exit_price)
            exit_fee = exit_notional * cfg.taker_fee_rate
            equity += after_slippage_gross - exit_fee
            current_trade_fee += exit_fee
            current_slippage_cost += exit_slippage
            current_turnover_notional += exit_notional
            net = equity - entry_equity
            holding_hours = float((pd.Timestamp(ts) - pd.Timestamp(entry_time)) / pd.Timedelta(hours=1)) if entry_time is not None else 0.0
            trades.append({
                "entry_time": entry_time,
                "exit_time": ts,
                "side": "LONG" if position > 0 else "SHORT",
                "entry_price": entry_price,
                "exit_price": exit_price,
                "entry_raw_price": entry_raw_price,
                "exit_raw_price": raw_price,
                "units": units,
                "pre_cost_gross_pnl": pre_cost_gross,
                "gross_pnl": after_slippage_gross,
                "slippage_cost": current_slippage_cost,
                "fee_cost": current_trade_fee,
                "funding_cost": current_funding_cost,
                "funding_income": current_funding_income,
                "turnover_notional": current_turnover_notional,
                "holding_hours": holding_hours,
                "net_pnl": net,
                "exit_reason": reason,
                "liquidation": liquidation,
            })
            position = 0
            units = 0.0
            entry_price = 0.0
            entry_raw_price = 0.0
            entry_time = None
            entry_equity = 0.0
            current_trade_fee = 0.0
            current_slippage_cost = 0.0
            current_turnover_notional = 0.0
            current_funding_cost = current_funding_income = 0.0

        current_row_only = bool(getattr(strategy, "uses_precomputed_features", False))
        for i, row in data.iterrows():
            ts = row["timestamp"]
            if ts > end:
                break
            in_eval = ts >= start
            # Prior candle signal executes at current candle open, never at the signal candle close.
            if in_eval:
                if pending_signal == SignalType.EXIT:
                    close_position(ts, float(row["open"]), pending_reason or "signal_exit")
                elif pending_signal in (SignalType.LONG, SignalType.SHORT):
                    desired = 1 if pending_signal == SignalType.LONG else -1
                    if position != 0 and position != desired:
                        close_position(ts, float(row["open"]), "reverse_signal")
                    if position == 0:
                        raw_open = float(row["open"])
                        fill = raw_open * (1 + cfg.slippage_rate if desired > 0 else 1 - cfg.slippage_rate)
                        size_fraction = cfg.position_size if pending_size_hint is None else min(cfg.position_size, max(0.0, float(pending_size_hint)))
                        notional = equity * size_fraction * cfg.leverage
                        if notional > 0:
                            units = notional / fill
                            entry_fee = notional * cfg.taker_fee_rate
                            entry_slippage = abs(units * (fill - raw_open))
                            entry_equity = equity
                            equity -= entry_fee
                            current_trade_fee = entry_fee
                            current_slippage_cost = entry_slippage
                            current_turnover_notional = notional
                            position = desired
                            entry_price = fill
                            entry_raw_price = raw_open
                            entry_time = ts
                if position != 0:
                    # Conservative isolated-margin liquidation approximation using intrabar extremes.
                    if position > 0:
                        liq_price = entry_price * (1 - 1 / cfg.leverage + cfg.maintenance_margin_rate)
                        if float(row["low"]) <= liq_price:
                            close_position(ts, liq_price, "conservative_liquidation", liquidation=True)
                    else:
                        liq_price = entry_price * (1 + 1 / cfg.leverage - cfg.maintenance_margin_rate)
                        if float(row["high"]) >= liq_price:
                            close_position(ts, liq_price, "conservative_liquidation", liquidation=True)
                if position != 0:
                    rate = float(row.get("funding_payment_rate", 0.0) or 0.0)
                    if rate:
                        notional = abs(units * float(row["close"]))
                        funding_pnl = -position * rate * notional
                        equity += funding_pnl
                        if funding_pnl < 0:
                            current_funding_cost += -funding_pnl
                        else:
                            current_funding_income += funding_pnl
            mark_equity = equity + (position * units * (float(row["close"]) - entry_price) if position != 0 else 0.0)
            if in_eval:
                curve.append({"timestamp": ts, "equity": mark_equity, "position": position})
            history = data.iloc[i : i + 1] if current_row_only else data.iloc[: i + 1]
            signal = strategy.generate_signal(history, str(row["symbol"]), position if in_eval else 0)
            signals.append(signal.to_dict())
            pending_signal = signal.signal if in_eval else SignalType.HOLD
            pending_reason = signal.entry_reason or signal.exit_reason
            pending_size_hint = signal.position_size_hint if in_eval else None

        if position != 0 and not data.empty:
            last = data[data["timestamp"] <= end].iloc[-1]
            close_position(last["close_time"], float(last["close"]), "end_of_test")
            if curve:
                curve[-1]["equity"] = equity
                curve[-1]["position"] = 0
        curve_df = pd.DataFrame(curve)
        trades_df = pd.DataFrame(trades)
        signals_df = pd.DataFrame(signals)
        metrics = calculate_metrics(curve_df, trades_df, cfg.initial_equity, cfg.periods_per_year)
        return BacktestResult(strategy.strategy_name, strategy.strategy_version, metrics, curve_df, trades_df, signals_df)


def save_result(result: BacktestResult, output_dir: str | Path, label: str) -> dict[str, str]:
    out = Path(output_dir) / result.strategy_name / result.strategy_version / label
    out.mkdir(parents=True, exist_ok=True)
    result.equity_curve.to_csv(out / "equity_curve.csv", index=False)
    result.trades.to_csv(out / "trades.csv", index=False)
    result.signals.to_csv(out / "signals.csv", index=False)
    with open(out / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(result.metrics, f, ensure_ascii=False, indent=2, allow_nan=False)
    return {"directory": str(out)}


def save_comparison(rows: list[dict], output_dir: str | Path) -> Path:
    target = Path(output_dir) / "strategy_comparison.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(target, index=False)
    return target


def buy_and_hold_metrics(data: pd.DataFrame, initial_equity: float = 10_000.0) -> dict:
    if data.empty:
        return {}
    start = float(data.iloc[0]["open"])
    end = float(data.iloc[-1]["close"])
    total_return = end / start - 1.0
    return {
        "benchmark": "BTC buy_and_hold_spot_like",
        "total_return": total_return,
        "ending_equity": initial_equity * (1 + total_return),
        "note": "Spot-like buy & hold comparator; no leverage or funding. Risk is not directly comparable to long/short futures strategies.",
    }
