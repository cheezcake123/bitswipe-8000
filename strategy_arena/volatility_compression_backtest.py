from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from strategy_arena.backtest import BacktestConfig, BacktestEngine, chronological_split
from strategy_arena.basis_execution import fetch_funding
from strategy_arena.funding_carry_v1 import FundingCarryV1Config
from strategy_arena.trend_breakout_backtest import attach_funding, load_hourly_futures
from strategy_arena.volatility_compression_v1 import VolatilityCompressionBreakoutV1


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
GROSS_CONFIG = BacktestConfig(
    initial_equity=INITIAL_EQUITY,
    position_size=1.0,
    leverage=2.0,
    taker_fee_rate=0.0,
    slippage_rate=0.0,
    maintenance_margin_rate=0.005,
    periods_per_year=365 * 24,
)


def add_precomputed_compression_features(hourly: pd.DataFrame) -> pd.DataFrame:
    """Compute frozen v1 ATR compression and breakout features from past-only data."""
    out = hourly.copy().sort_values("timestamp").reset_index(drop=True)
    prev_close = out["close"].shift(1)
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr_pct = tr.rolling(24, min_periods=24).mean() / out["close"]
    known_atr = atr_pct.shift(1)
    threshold = known_atr.rolling(90 * 24, min_periods=30 * 24).quantile(0.20)
    compressed = known_atr <= threshold
    upper = out["high"].rolling(24, min_periods=24).max().shift(1)
    lower = out["low"].rolling(24, min_periods=24).min().shift(1)

    out["vc_atr_pct"] = known_atr
    out["vc_compression_threshold"] = threshold
    out["vc_compressed"] = compressed.fillna(False)
    out["vc_range_upper"] = upper
    out["vc_range_lower"] = lower
    out["vc_entry_long"] = out["vc_compressed"] & (out["close"] > upper)
    out["vc_entry_short"] = out["vc_compressed"] & (out["close"] < lower)
    out["vc_ready"] = known_atr.notna() & threshold.notna() & upper.notna() & lower.notna()
    return out


def _safe_number(value):
    if isinstance(value, (float, np.floating)) and (math.isnan(float(value)) or math.isinf(float(value))):
        return None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    return value


def safe_dict(data: dict) -> dict:
    return {k: _safe_number(v) for k, v in data.items()}


def profit_factor(values: pd.Series) -> float | None:
    if values.empty:
        return None
    wins = float(values[values > 0].sum())
    losses = float(-values[values < 0].sum())
    if losses == 0:
        return None if wins == 0 else math.inf
    return wins / losses


def enrich_trades(trades: pd.DataFrame, hourly: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return trades.copy()
    out = trades.copy()
    h = hourly.set_index("timestamp").sort_index()
    mfe, mae = [], []
    for _, trade in out.iterrows():
        start = pd.Timestamp(trade["entry_time"])
        end = pd.Timestamp(trade["exit_time"])
        window = h.loc[(h.index >= start) & (h.index <= end)]
        entry = float(trade["entry_raw_price"])
        if window.empty or entry <= 0:
            mfe.append(np.nan)
            mae.append(np.nan)
        elif trade["side"] == "LONG":
            mfe.append(float(window["high"].max() / entry - 1.0))
            mae.append(float(window["low"].min() / entry - 1.0))
        else:
            mfe.append(float(1.0 - window["low"].min() / entry))
            mae.append(float(1.0 - window["high"].max() / entry))
    out["mfe"] = mfe
    out["mae"] = mae
    out["false_breakout"] = out["pre_cost_gross_pnl"] <= 0
    return out


def extended_metrics(metrics: dict, trades: pd.DataFrame) -> dict:
    result = safe_dict(metrics)
    result.update({
        "median_holding_hours": float(trades["holding_hours"].median()) if not trades.empty else 0.0,
        "mean_mfe": float(trades["mfe"].mean()) if not trades.empty and "mfe" in trades else 0.0,
        "mean_mae": float(trades["mae"].mean()) if not trades.empty and "mae" in trades else 0.0,
        "false_breakout_rate": float(trades["false_breakout"].mean()) if not trades.empty and "false_breakout" in trades else 0.0,
        "pre_cost_gross_pnl": float(trades["pre_cost_gross_pnl"].sum()) if not trades.empty else 0.0,
        "net_pnl": float(trades["net_pnl"].sum()) if not trades.empty else 0.0,
    })
    return result


def run_pair(data: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, direction_mode: str = "SHORT_ONLY"):
    gross_strategy = VolatilityCompressionBreakoutV1(direction_mode=direction_mode)
    net_strategy = VolatilityCompressionBreakoutV1(direction_mode=direction_mode)
    no_funding = data.copy()
    no_funding["funding_payment_rate"] = 0.0
    gross = BacktestEngine(GROSS_CONFIG).run(no_funding, gross_strategy, start, end)
    net = BacktestEngine(NET_CONFIG).run(data, net_strategy, start, end)
    gross.trades = enrich_trades(gross.trades, data)
    net.trades = enrich_trades(net.trades, data)
    return gross, net


def build_regime_frame(hourly: pd.DataFrame, funding: pd.DataFrame) -> pd.DataFrame:
    out = hourly[["timestamp", "close"]].copy().sort_values("timestamp").reset_index(drop=True)
    trend_ret = out["close"] / out["close"].shift(7 * 24) - 1.0
    out["trend_regime"] = np.select(
        [trend_ret >= 0.05, trend_ret <= -0.05],
        ["BULL", "BEAR"],
        default="SIDEWAYS",
    )
    out.loc[trend_ret.isna(), "trend_regime"] = "UNCLASSIFIED"

    log_ret = np.log(out["close"] / out["close"].shift(1))
    current_rv = log_ret.rolling(24, min_periods=24).std(ddof=0) * np.sqrt(24)
    future_rv = log_ret.rolling(24, min_periods=24).std(ddof=0).shift(-24) * np.sqrt(24)
    out["expansion_regime"] = np.where(future_rv > current_rv, "HIGH_VOLATILITY_EXPANSION", "NO_EXPANSION")
    out.loc[current_rv.isna() | future_rv.isna(), "expansion_regime"] = "UNCLASSIFIED"

    if funding.empty:
        out["funding_regime"] = "NORMAL_FUNDING"
    else:
        f = funding[["timestamp", "funding_rate"]].copy().sort_values("timestamp")
        f["timestamp"] = pd.to_datetime(f["timestamp"], utc=True).astype("datetime64[ms, UTC]")
        left = out[["timestamp"]].copy()
        left["timestamp"] = pd.to_datetime(left["timestamp"], utc=True).astype("datetime64[ms, UTC]")
        rate = pd.merge_asof(left, f, on="timestamp", direction="backward", allow_exact_matches=True)["funding_rate"]
        out["funding_regime"] = np.where(rate.to_numpy() >= FundingCarryV1Config().entry_funding_rate, "HIGH_FUNDING", "NORMAL_FUNDING")
    return out


def trade_attribution(trades: pd.DataFrame, regimes: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    t = trades.copy()
    t["entry_time"] = pd.to_datetime(t["entry_time"], utc=True).astype("datetime64[ms, UTC]")
    r = regimes.copy()
    r["timestamp"] = pd.to_datetime(r["timestamp"], utc=True).astype("datetime64[ms, UTC]")
    merged = t.merge(r.rename(columns={"timestamp": "entry_time"}), on="entry_time", how="left")
    rows = []
    for col in ["trend_regime", "funding_regime", "expansion_regime"]:
        for value, g in merged.groupby(col, dropna=False):
            rows.append({
                "dimension": col,
                "regime": str(value),
                "trades": int(len(g)),
                "net_pnl": float(g["net_pnl"].sum()),
                "pre_cost_gross_pnl": float(g["pre_cost_gross_pnl"].sum()),
                "win_rate": float((g["net_pnl"] > 0).mean()),
                "profit_factor": profit_factor(g["net_pnl"]),
                "fee_cost": float(g["fee_cost"].sum()),
                "slippage_cost": float(g["slippage_cost"].sum()),
                "funding_net": float(g["funding_income"].sum() - g["funding_cost"].sum()),
                "average_holding_hours": float(g["holding_hours"].mean()),
                "false_breakout_rate": float(g["false_breakout"].mean()),
            })
    return pd.DataFrame(rows)


def concentration_stats(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {}
    positive = trades[trades["net_pnl"] > 0].sort_values("net_pnl", ascending=False)
    total_positive = float(positive["net_pnl"].sum())
    result = {}
    for n in (1, 3, 5):
        removed = set(positive.head(n).index)
        remaining_net = float(trades.loc[~trades.index.isin(removed), "net_pnl"].sum())
        result[f"top{n}_share_of_positive_pnl"] = float(positive.head(n)["net_pnl"].sum() / total_positive) if total_positive > 0 else None
        result[f"net_return_after_removing_top{n}"] = remaining_net / INITIAL_EQUITY
    return result


def write_result(out: Path, label: str, gross, net) -> dict:
    target = out / label
    target.mkdir(parents=True, exist_ok=True)
    gross.trades.to_csv(target / "gross_trades.csv", index=False)
    net.trades.to_csv(target / "net_trades.csv", index=False)
    net.equity_curve.to_csv(target / "net_equity_curve.csv", index=False)
    net.signals.to_csv(target / "signals.csv", index=False)
    payload = {
        "gross": extended_metrics(gross.metrics, gross.trades),
        "net": extended_metrics(net.metrics, net.trades),
    }
    (target / "metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    p = argparse.ArgumentParser(description="Fixed-rule Volatility Compression Breakout v1 backtest; research only")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--start-month", default="2019-09")
    p.add_argument("--end-month", default="2026-06")
    p.add_argument("--evaluation-start", default="2020-01-01T00:00:00Z")
    p.add_argument("--output-dir", default="data/arena/volatility_compression_v1")
    p.add_argument("--workers", type=int, default=6)
    args = p.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    hourly_raw = load_hourly_futures(args.symbol, args.start_month, args.end_month, args.workers)
    hourly = add_precomputed_compression_features(hourly_raw)
    funding = fetch_funding(args.symbol, args.start_month, args.end_month)
    data = attach_funding(hourly, funding)
    regimes = build_regime_frame(hourly_raw, funding)

    eval_start = pd.Timestamp(args.evaluation_start)
    eval_end = data["timestamp"].max()
    eval_data = data[data["timestamp"] >= eval_start]
    splits = chronological_split(eval_data)
    oos = next(s for s in splits if s.name == "oos")

    # Frozen v1: SHORT_ONLY. Final OOS is evaluated once with these rules.
    gross_full, net_full = run_pair(data, eval_start, eval_end, "SHORT_ONLY")
    full_metrics = write_result(out, "full_2020_2026", gross_full, net_full)

    split_rows = []
    split_payload = {}
    for split in splits:
        gross, net = run_pair(data, split.start, split.end, "SHORT_ONLY")
        payload = write_result(out, split.name, gross, net)
        split_payload[split.name] = payload
        split_rows.append({
            "split": split.name,
            "start": split.start.isoformat(),
            "end": split.end.isoformat(),
            "gross_total_return": payload["gross"].get("total_return"),
            "net_total_return": payload["net"].get("total_return"),
            "net_mdd": payload["net"].get("maximum_drawdown"),
            "net_sharpe": payload["net"].get("sharpe"),
            "net_profit_factor": payload["net"].get("profit_factor"),
            "trades": payload["net"].get("number_of_trades"),
        })
    pd.DataFrame(split_rows).to_csv(out / "split_performance.csv", index=False)

    yearly_rows = []
    for year in range(2020, 2027):
        start = pd.Timestamp(f"{year}-01-01T00:00:00Z")
        end = min(pd.Timestamp(f"{year}-12-31T23:00:00Z"), eval_end)
        if start > eval_end:
            continue
        gross, net = run_pair(data, start, end, "SHORT_ONLY")
        g = extended_metrics(gross.metrics, gross.trades)
        n = extended_metrics(net.metrics, net.trades)
        yearly_rows.append({"year": year, "period_end": end.isoformat(), **{f"gross_{k}": v for k, v in g.items()}, **{f"net_{k}": v for k, v in n.items()}})
    pd.DataFrame(yearly_rows).to_csv(out / "yearly_performance.csv", index=False)

    recent_start = pd.Timestamp("2024-01-01T00:00:00Z")
    gross_recent, net_recent = run_pair(data, recent_start, eval_end, "SHORT_ONLY")
    recent_metrics = write_result(out, "recent_2024_2026", gross_recent, net_recent)

    direction_rows = []
    for mode in ("LONG_ONLY", "SHORT_ONLY", "BOTH"):
        for period, start, end in (
            ("full_2020_2026", eval_start, eval_end),
            ("recent_2024_2026", recent_start, eval_end),
            ("final_oos", oos.start, oos.end),
        ):
            gross, net = run_pair(data, start, end, mode)
            gm = extended_metrics(gross.metrics, gross.trades)
            nm = extended_metrics(net.metrics, net.trades)
            direction_rows.append({
                "direction_mode": mode,
                "period": period,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "gross_total_return": gm.get("total_return"),
                "net_total_return": nm.get("total_return"),
                "net_mdd": nm.get("maximum_drawdown"),
                "net_sharpe": nm.get("sharpe"),
                "net_profit_factor": nm.get("profit_factor"),
                "trades": nm.get("number_of_trades"),
                "funding_cost": nm.get("funding_cost"),
                "funding_income": nm.get("funding_income"),
                "average_holding_hours": nm.get("average_holding_hours"),
                "false_breakout_rate": nm.get("false_breakout_rate"),
            })
    pd.DataFrame(direction_rows).to_csv(out / "direction_comparison.csv", index=False)

    attribution = trade_attribution(net_full.trades, regimes)
    attribution.to_csv(out / "regime_attribution.csv", index=False)

    expected_hours = int((hourly_raw["timestamp"].max() - hourly_raw["timestamp"].min()) / pd.Timedelta(hours=1)) + 1
    summary = {
        "strategy": "volatility_compression_breakout",
        "version": "v1",
        "frozen_rule": asdict(VolatilityCompressionBreakoutV1()),
        "selected_direction": "SHORT_ONLY",
        "selection_basis": "Pre-strategy fixed-criteria relationship study: ATR compression had stronger 24h directional edge than BB; 2024-2026 SHORT retained positive 24h edge while LONG weakened; recent SHORT 72h edge reversed, motivating a 24h time stop.",
        "parameter_optimization": False,
        "data": {
            "start": hourly_raw["timestamp"].min().isoformat(),
            "end": hourly_raw["timestamp"].max().isoformat(),
            "hourly_rows": int(len(hourly_raw)),
            "expected_hours": expected_hours,
            "missing_hours": int(expected_hours - len(hourly_raw)),
            "funding_events": int(len(funding)),
            "source": "Binance Vision USD-M official public archives",
        },
        "full_2020_2026": full_metrics,
        "recent_2024_2026": recent_metrics,
        "final_oos": split_payload["oos"],
        "splits": split_rows,
        "concentration": concentration_stats(net_full.trades),
        "risk_notes": [
            "The v1 is SHORT-only; LONG-only and BOTH are comparison runs, not alternate optimized v1 variants.",
            "Exit is the first of: close re-enters the original breakout range, or fixed 24h time stop. Signals execute at next hourly open through the common engine.",
            "Gross removes fee, slippage and funding. Net uses common 5bp taker fee, 5bp slippage, 2x leverage and archived funding events.",
            "HIGH_VOLATILITY_EXPANSION attribution is retrospective (future 24h realized volatility > entry-time trailing volatility) and is never used as a trading signal.",
            "Long-horizon historical OI coverage is insufficient in the current free official dataset, so no High-OI filter is used or claimed.",
            "Liquidation safety is the same conservative common-engine approximation used by other Strategy Arena strategies.",
        ],
        "existing_strategies_modified": False,
        "funding_carry_entry_rate_frozen": FundingCarryV1Config().entry_funding_rate,
        "live_trading": False,
        "actual_orders": False,
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
