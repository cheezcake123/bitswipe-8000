from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from strategy_arena.backtest import BacktestConfig, BacktestEngine, chronological_split
from strategy_arena.basis_execution import fetch_funding
from strategy_arena.funding_carry_v1 import FundingCarryV1Config
from strategy_arena.strategies import FundingExtremeReversalV1, Signal, SignalType
from strategy_arena.trend_breakout_backtest import load_hourly_futures

INITIAL_EQUITY = 10_000.0
NET_CONFIG = BacktestConfig(
    initial_equity=INITIAL_EQUITY,
    position_size=1.0,
    leverage=2.0,
    taker_fee_rate=0.0005,
    slippage_rate=0.0005,
    maintenance_margin_rate=0.005,
    periods_per_year=365 * 24,
)
PRICE_CONFIG = BacktestConfig(
    initial_equity=INITIAL_EQUITY,
    position_size=1.0,
    leverage=2.0,
    taker_fee_rate=0.0,
    slippage_rate=0.0,
    maintenance_margin_rate=0.005,
    periods_per_year=365 * 24,
)


@dataclass
class DirectionFilteredFundingExtreme:
    """Adapter only for evaluation. It delegates every signal decision to the frozen v1.

    LONG_ONLY and SHORT_ONLY suppress the opposite entry signal. Exit logic, thresholds,
    momentum logic, z-score calculation, and all parameters remain owned by the original
    FundingExtremeReversalV1 implementation.
    """

    direction_mode: str = "BOTH"

    def __post_init__(self) -> None:
        self.inner = FundingExtremeReversalV1()
        self.strategy_name = self.inner.strategy_name
        self.strategy_version = self.inner.strategy_version
        self.max_lookback_bars = self.inner.max_lookback_bars

    def generate_signal(self, history: pd.DataFrame, symbol: str, current_position: int) -> Signal:
        signal = self.inner.generate_signal(history, symbol, current_position)
        if current_position == 0:
            if self.direction_mode == "LONG_ONLY" and signal.signal == SignalType.SHORT:
                return Signal(signal.strategy_name, signal.strategy_version, signal.timestamp, symbol, SignalType.HOLD, metadata={**signal.metadata, "direction_filter": "LONG_ONLY"})
            if self.direction_mode == "SHORT_ONLY" and signal.signal == SignalType.LONG:
                return Signal(signal.strategy_name, signal.strategy_version, signal.timestamp, symbol, SignalType.HOLD, metadata={**signal.metadata, "direction_filter": "SHORT_ONLY"})
        return signal


def _safe(value):
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        value = float(value)
        return None if not np.isfinite(value) else value
    return value


def safe_dict(data: dict) -> dict:
    return {k: _safe(v) for k, v in data.items()}


def build_long_funding_dataset(hourly: pd.DataFrame, funding: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Build the long-history funding dataset with backward-only known funding.

    Signal feature:
      funding_rate = last funding event with funding timestamp <= candle close_time.
    Cash-flow feature:
      funding_payment_rate = funding event(s) whose funding timestamp falls in the UTC
      hour identified by the candle open timestamp. This correctly books payments at
      00:00/08:00/16:00 while never using them before they occur for signal generation.
    """
    out = hourly.copy().sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True).astype("datetime64[ms, UTC]")
    out["close_time"] = pd.to_datetime(out["close_time"], utc=True).astype("datetime64[ms, UTC]")
    out["decision_timestamp"] = out["close_time"]
    out["symbol"] = symbol

    f = funding[["timestamp", "funding_rate"]].copy().sort_values("timestamp").drop_duplicates("timestamp")
    f["timestamp"] = pd.to_datetime(f["timestamp"], utc=True).astype("datetime64[ms, UTC]")
    known = f.rename(columns={"timestamp": "funding_available_at"})
    out = pd.merge_asof(
        out.sort_values("decision_timestamp"),
        known,
        left_on="decision_timestamp",
        right_on="funding_available_at",
        direction="backward",
        allow_exact_matches=True,
    )

    f["payment_hour"] = f["timestamp"].dt.floor("h")
    payments = f.groupby("payment_hour", as_index=True)["funding_rate"].sum()
    out["funding_payment_rate"] = out["timestamp"].map(payments).fillna(0.0).astype(float)
    return out.sort_values("timestamp").reset_index(drop=True)


def validate_no_lookahead(data: pd.DataFrame, funding: pd.DataFrame) -> dict:
    known_mask = data["funding_available_at"].notna()
    future_rows = int((data.loc[known_mask, "funding_available_at"] > data.loc[known_mask, "decision_timestamp"]).sum())
    f = funding[["timestamp", "funding_rate"]].copy().sort_values("timestamp")
    checks = []
    sample_idx = np.linspace(0, len(data) - 1, min(250, len(data)), dtype=int)
    for i in sample_idx:
        row = data.iloc[i]
        eligible = f[f["timestamp"] <= row["decision_timestamp"]]
        expected = float(eligible.iloc[-1]["funding_rate"]) if not eligible.empty else np.nan
        actual = row["funding_rate"]
        checks.append(bool((pd.isna(expected) and pd.isna(actual)) or np.isclose(float(actual), expected, rtol=0, atol=1e-15)))
    return {
        "future_funding_rows": future_rows,
        "sampled_backward_asof_checks": len(checks),
        "sampled_backward_asof_pass": bool(all(checks)),
        "rolling_window_definition": "FundingExtremeReversalV1 uses history.tail(168), so every z-score window contains only rows up to the current decision candle.",
        "pass": future_rows == 0 and all(checks),
    }


def enrich_trades(trades: pd.DataFrame, hourly: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return trades.copy()
    h = hourly.set_index("timestamp").sort_index()
    out = trades.copy()
    mfes, maes = [], []
    for _, trade in out.iterrows():
        start, end = pd.Timestamp(trade["entry_time"]), pd.Timestamp(trade["exit_time"])
        window = h.loc[(h.index >= start) & (h.index <= end)]
        entry = float(trade["entry_raw_price"])
        if window.empty or entry <= 0:
            mfes.append(np.nan); maes.append(np.nan)
        elif trade["side"] == "LONG":
            mfes.append(float(window["high"].max() / entry - 1.0))
            maes.append(float(window["low"].min() / entry - 1.0))
        else:
            mfes.append(float(1.0 - window["low"].min() / entry))
            maes.append(float(1.0 - window["high"].max() / entry))
    out["mfe"] = mfes
    out["mae"] = maes
    return out


def extended_metrics(result, hourly: pd.DataFrame) -> dict:
    trades = enrich_trades(result.trades, hourly)
    m = safe_dict(result.metrics)
    m.update({
        "median_holding_hours": float(trades["holding_hours"].median()) if not trades.empty else 0.0,
        "mean_mfe": float(trades["mfe"].mean()) if not trades.empty else 0.0,
        "mean_mae": float(trades["mae"].mean()) if not trades.empty else 0.0,
        "price_pnl": float(trades["pre_cost_gross_pnl"].sum()) if not trades.empty else 0.0,
        "funding_net": float(trades["funding_income"].sum() - trades["funding_cost"].sum()) if not trades.empty else 0.0,
        "net_pnl": float(trades["net_pnl"].sum()) if not trades.empty else 0.0,
    })
    return m


def run_three_stage(data: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, direction: str):
    strategy_price = DirectionFilteredFundingExtreme(direction)
    strategy_funding = DirectionFilteredFundingExtreme(direction)
    strategy_net = DirectionFilteredFundingExtreme(direction)

    price_data = data.copy(); price_data["funding_payment_rate"] = 0.0
    price = BacktestEngine(PRICE_CONFIG).run(price_data, strategy_price, start, end)
    funding_gross = BacktestEngine(PRICE_CONFIG).run(data, strategy_funding, start, end)
    net = BacktestEngine(NET_CONFIG).run(data, strategy_net, start, end)
    return price, funding_gross, net


def concentration_stats(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {}
    positive = trades[trades["net_pnl"] > 0].sort_values("net_pnl", ascending=False)
    total_pos = float(positive["net_pnl"].sum())
    out = {}
    for n in (1, 3, 5):
        removed = positive.head(n).index
        remaining = float(trades.loc[~trades.index.isin(removed), "net_pnl"].sum())
        out[f"top{n}_share_of_positive_pnl"] = float(positive.head(n)["net_pnl"].sum() / total_pos) if total_pos > 0 else None
        out[f"net_return_after_removing_top{n}"] = remaining / INITIAL_EQUITY
    return out


def signal_extreme_stats(signals: pd.DataFrame) -> dict:
    if signals.empty or "metadata" not in signals.columns:
        return {}
    rows = []
    for _, row in signals.iterrows():
        meta = row.get("metadata") or {}
        z = meta.get("funding_zscore") if isinstance(meta, dict) else None
        sig = row.get("signal")
        if z is not None and sig in ("LONG", "SHORT"):
            rows.append((sig, abs(float(z))))
    if not rows:
        return {"entry_signals": 0, "mean_abs_entry_zscore": None}
    arr = pd.DataFrame(rows, columns=["signal", "abs_z"])
    return {
        "entry_signals": int(len(arr)),
        "long_signals": int((arr["signal"] == "LONG").sum()),
        "short_signals": int((arr["signal"] == "SHORT").sum()),
        "mean_abs_entry_zscore": float(arr["abs_z"].mean()),
    }


def regime_frame(data: pd.DataFrame) -> pd.DataFrame:
    out = data[["timestamp", "close", "funding_rate"]].copy()
    trend_ret = out["close"] / out["close"].shift(7 * 24) - 1.0
    out["trend_regime"] = np.select([trend_ret >= 0.05, trend_ret <= -0.05], ["BULL", "BEAR"], default="SIDEWAYS")
    logret = np.log(out["close"] / out["close"].shift(1))
    rv = logret.rolling(24, min_periods=24).std(ddof=0)
    rv_ref = rv.shift(1).rolling(30 * 24, min_periods=7 * 24).median()
    out["vol_regime"] = np.where(rv >= rv_ref, "HIGH_VOLATILITY", "LOW_VOLATILITY")
    out["funding_extreme_regime"] = "NORMAL_FUNDING"
    return out


def attach_entry_z(trades: pd.DataFrame, signals: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return trades.copy()
    s = signals.copy()
    s["timestamp"] = pd.to_datetime(s["timestamp"], utc=True)
    s["signal_time_hour"] = s["timestamp"].dt.floor("h")
    zs = []
    for meta in s["metadata"]:
        zs.append(meta.get("funding_zscore") if isinstance(meta, dict) else np.nan)
    s["funding_zscore"] = zs
    s = s[s["signal"].isin(["LONG", "SHORT"])][["signal_time_hour", "signal", "funding_zscore"]]
    t = trades.copy()
    t["entry_signal_hour"] = pd.to_datetime(t["entry_time"], utc=True).dt.floor("h") - pd.Timedelta(hours=1)
    return t.merge(s, left_on=["entry_signal_hour", "side"], right_on=["signal_time_hour", "signal"], how="left")


def regime_attribution(trades: pd.DataFrame, signals: pd.DataFrame, regimes: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    t = attach_entry_z(trades, signals)
    t["entry_hour"] = pd.to_datetime(t["entry_time"], utc=True).dt.floor("h")
    r = regimes.rename(columns={"timestamp": "entry_hour"})
    t = t.merge(r[["entry_hour", "trend_regime", "vol_regime"]], on="entry_hour", how="left")
    t["funding_side_regime"] = np.where(t["side"] == "SHORT", "EXTREME_POSITIVE_FUNDING_SHORT", "EXTREME_NEGATIVE_FUNDING_LONG")
    rows = []
    for col in ("trend_regime", "vol_regime", "funding_side_regime"):
        for value, g in t.groupby(col, dropna=False):
            rows.append({
                "dimension": col,
                "regime": str(value),
                "trades": int(len(g)),
                "net_pnl": float(g["net_pnl"].sum()),
                "win_rate": float((g["net_pnl"] > 0).mean()),
                "funding_net": float(g["funding_income"].sum() - g["funding_cost"].sum()),
                "mean_abs_entry_zscore": float(g["funding_zscore"].abs().mean()) if g["funding_zscore"].notna().any() else None,
                "average_holding_hours": float(g["holding_hours"].mean()),
            })
    return pd.DataFrame(rows)


def carry_overlap(data: pd.DataFrame, signals: pd.DataFrame) -> dict:
    s = signals.copy()
    s["timestamp"] = pd.to_datetime(s["timestamp"], utc=True)
    short_signals = s[s["signal"] == "SHORT"]
    if short_signals.empty:
        return {"funding_extreme_short_signals": 0, "carry_high_funding_overlap": 0, "overlap_rate": 0.0, "opposite_position_conflicts": 0}
    d = data[["decision_timestamp", "funding_rate"]].copy()
    merged = short_signals.merge(d, left_on="timestamp", right_on="decision_timestamp", how="left")
    high = merged["funding_rate"] >= FundingCarryV1Config().entry_funding_rate
    overlap = int(high.sum())
    # Carry is Spot LONG + Perp SHORT; Funding Extreme SHORT is also Perp SHORT direction,
    # so they do not request opposite perpetual directions when overlapping.
    return {
        "funding_extreme_short_signals": int(len(short_signals)),
        "carry_high_funding_overlap": overlap,
        "overlap_rate": float(overlap / len(short_signals)),
        "opposite_position_conflicts": 0,
        "note": "Funding Carry is delta-neutral Spot LONG + Perp SHORT; overlapping Funding Extreme SHORT reinforces the perpetual short leg rather than opposing it, but combined exposure/margin would require selector coordination.",
    }


def write_set(out: Path, label: str, price, funding_gross, net, hourly: pd.DataFrame) -> dict:
    target = out / label
    target.mkdir(parents=True, exist_ok=True)
    net.trades.to_csv(target / "net_trades.csv", index=False)
    net.signals.to_csv(target / "signals.csv", index=False)
    net.equity_curve.to_csv(target / "net_equity_curve.csv", index=False)
    payload = {
        "price_only": extended_metrics(price, hourly),
        "funding_gross": extended_metrics(funding_gross, hourly),
        "net": extended_metrics(net, hourly),
    }
    (target / "metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    p = argparse.ArgumentParser(description="Frozen Funding Extreme Reversal v1 long-horizon validation")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--start-month", default="2019-09")
    p.add_argument("--end-month", default="2026-06")
    p.add_argument("--evaluation-start", default="2020-01-01T00:00:00Z")
    p.add_argument("--output-dir", default="data/arena/funding_extreme_long_validation")
    p.add_argument("--workers", type=int, default=6)
    args = p.parse_args()

    out = Path(args.output_dir); out.mkdir(parents=True, exist_ok=True)
    hourly = load_hourly_futures(args.symbol, args.start_month, args.end_month, args.workers)
    funding = fetch_funding(args.symbol, args.start_month, args.end_month)
    data = build_long_funding_dataset(hourly, funding, args.symbol)
    lookahead = validate_no_lookahead(data, funding)
    eval_start = pd.Timestamp(args.evaluation_start)
    eval_end = data["timestamp"].max()
    eval_data = data[data["timestamp"] >= eval_start]
    splits = chronological_split(eval_data)

    price_full, funding_full, net_full = run_three_stage(data, eval_start, eval_end, "BOTH")
    full_payload = write_set(out, "full_2020_2026", price_full, funding_full, net_full, hourly)

    direction_payload = {}
    for mode in ("LONG_ONLY", "SHORT_ONLY", "BOTH"):
        price, gross, net = run_three_stage(data, eval_start, eval_end, mode)
        direction_payload[mode] = write_set(out, f"direction_{mode.lower()}", price, gross, net, hourly)

    split_rows = []
    split_payload = {}
    for split in splits:
        price, gross, net = run_three_stage(data, split.start, split.end, "BOTH")
        payload = write_set(out, split.name, price, gross, net, hourly)
        split_payload[split.name] = payload
        split_rows.append({"split": split.name, "start": split.start.isoformat(), "end": split.end.isoformat(), "net_total_return": payload["net"].get("total_return"), "net_mdd": payload["net"].get("maximum_drawdown"), "net_sharpe": payload["net"].get("sharpe"), "net_profit_factor": payload["net"].get("profit_factor"), "trades": payload["net"].get("number_of_trades")})
    pd.DataFrame(split_rows).to_csv(out / "split_performance.csv", index=False)

    yearly_rows = []
    extreme_rows = []
    for year in range(2020, 2027):
        start = pd.Timestamp(f"{year}-01-01T00:00:00Z")
        end = min(pd.Timestamp(f"{year}-12-31T23:00:00Z"), eval_end)
        if start > eval_end: continue
        price, gross, net = run_three_stage(data, start, end, "BOTH")
        m = extended_metrics(net, hourly)
        yearly_rows.append({"year": year, **m})
        extreme_rows.append({"year": year, **signal_extreme_stats(net.signals)})
    pd.DataFrame(yearly_rows).to_csv(out / "yearly_performance.csv", index=False)
    pd.DataFrame(extreme_rows).to_csv(out / "yearly_extreme_environment.csv", index=False)

    recent_start = pd.Timestamp("2024-01-01T00:00:00Z")
    price_recent, gross_recent, net_recent = run_three_stage(data, recent_start, eval_end, "BOTH")
    recent_payload = write_set(out, "recent_2024_2026", price_recent, gross_recent, net_recent, hourly)

    regimes = regime_frame(data)
    attribution = regime_attribution(net_full.trades, net_full.signals, regimes)
    attribution.to_csv(out / "regime_attribution.csv", index=False)
    concentration = concentration_stats(net_full.trades)
    overlap = carry_overlap(data, net_full.signals)

    expected_hours = int((hourly["timestamp"].max() - hourly["timestamp"].min()) / pd.Timedelta(hours=1)) + 1
    frozen = FundingExtremeReversalV1()
    summary = {
        "strategy": frozen.strategy_name,
        "version": frozen.strategy_version,
        "frozen_parameters": asdict(frozen),
        "strategy_logic_changed": False,
        "direction_adapters_change_original_v1": False,
        "parameter_optimization": False,
        "data": {"start": hourly["timestamp"].min().isoformat(), "end": hourly["timestamp"].max().isoformat(), "evaluation_start": eval_start.isoformat(), "hourly_rows": int(len(hourly)), "expected_hours": expected_hours, "missing_hours": int(expected_hours - len(hourly)), "funding_events": int(len(funding)), "timestamp_alignment": "funding_rate backward-asof to candle close; funding payments booked in their actual UTC funding hour"},
        "lookahead_validation": lookahead,
        "full_2020_2026": full_payload,
        "directions": direction_payload,
        "splits": split_payload,
        "recent_2024_2026": recent_payload,
        "concentration": concentration,
        "funding_carry_relationship": overlap,
        "live_trading": False,
        "actual_orders": False,
        "notes": [
            "The original FundingExtremeReversalV1 class is called directly; its 168/1.75/6/0.5 parameters and signal logic are unchanged.",
            "Direction-only evaluations suppress opposite entries solely for attribution and do not modify the original BOTH-direction v1.",
            "Signals are decided after candle close and the common BacktestEngine executes them at the next candle open.",
            "Price-only removes funding, fees and slippage; Funding Gross adds actual funding cash flows without fees/slippage; Net adds common fees and slippage.",
            "Funding signal information uses only the latest event already paid by the decision timestamp; no future funding rate is forward-filled backward.",
        ],
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
