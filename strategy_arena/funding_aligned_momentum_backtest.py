from __future__ import annotations

import argparse
import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from strategy_arena.archive_cli import fetch_vision_funding, fetch_vision_ohlcv, fetch_vision_oi
from strategy_arena.backtest import BacktestConfig, BacktestEngine, chronological_split
from strategy_arena.basis_execution import fetch_funding
from strategy_arena.data import build_research_dataset
from strategy_arena.funding_aligned_momentum_research import attach_known_funding
from strategy_arena.funding_aligned_momentum_v1 import FundingAlignedMomentumV1
from strategy_arena.funding_carry_v1 import FundingCarryV1Config
from strategy_arena.strategies import FundingExtremeReversalV1, OIMomentumV1
from strategy_arena.trend_breakout_backtest import attach_funding, load_hourly_futures
from strategy_arena.volatility_compression_backtest import add_precomputed_compression_features
from strategy_arena.volatility_compression_v1 import VolatilityCompressionBreakoutV1


INITIAL_EQUITY = 10_000.0
PRICE_ONLY_CONFIG = BacktestConfig(
    initial_equity=INITIAL_EQUITY,
    position_size=1.0,
    leverage=2.0,
    taker_fee_rate=0.0,
    slippage_rate=0.0,
    maintenance_margin_rate=0.005,
    periods_per_year=365 * 24,
)
FUNDING_GROSS_CONFIG = PRICE_ONLY_CONFIG
NET_CONFIG = BacktestConfig(
    initial_equity=INITIAL_EQUITY,
    position_size=1.0,
    leverage=2.0,
    taker_fee_rate=0.0005,
    slippage_rate=0.0005,
    maintenance_margin_rate=0.005,
    periods_per_year=365 * 24,
)


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


def prepare_data(hourly: pd.DataFrame, funding: pd.DataFrame) -> pd.DataFrame:
    out = attach_known_funding(hourly, funding)
    out = out.rename(columns={
        "known_funding_timestamp": "fam_known_funding_timestamp",
        "known_funding_rate": "fam_known_funding_rate",
    })
    out["fam_momentum_return"] = out["close"] / out["close"].shift(168) - 1.0
    out["fam_ready"] = out["fam_momentum_return"].notna() & out["fam_known_funding_timestamp"].notna()
    out = attach_funding(out, funding)
    return out


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
    return out


def extended_metrics(metrics: dict, trades: pd.DataFrame) -> dict:
    result = safe_dict(metrics)
    result.update({
        "median_holding_hours": float(trades["holding_hours"].median()) if not trades.empty else 0.0,
        "mean_mfe": float(trades["mfe"].mean()) if not trades.empty and "mfe" in trades else 0.0,
        "mean_mae": float(trades["mae"].mean()) if not trades.empty and "mae" in trades else 0.0,
        "pre_cost_gross_pnl": float(trades["pre_cost_gross_pnl"].sum()) if not trades.empty else 0.0,
        "net_pnl": float(trades["net_pnl"].sum()) if not trades.empty else 0.0,
    })
    return result


def _slice(data: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    return data[(data["timestamp"] >= start) & (data["timestamp"] <= end)].copy().reset_index(drop=True)


def run_three(data: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, direction_mode: str = "LONG_ONLY"):
    sample = _slice(data, start, end)
    no_funding = sample.copy()
    no_funding["funding_payment_rate"] = 0.0

    price_only = BacktestEngine(PRICE_ONLY_CONFIG).run(no_funding, FundingAlignedMomentumV1(direction_mode=direction_mode))
    funding_gross = BacktestEngine(FUNDING_GROSS_CONFIG).run(sample, FundingAlignedMomentumV1(direction_mode=direction_mode))
    net = BacktestEngine(NET_CONFIG).run(sample, FundingAlignedMomentumV1(direction_mode=direction_mode))

    price_only.trades = enrich_trades(price_only.trades, sample)
    funding_gross.trades = enrich_trades(funding_gross.trades, sample)
    net.trades = enrich_trades(net.trades, sample)
    return price_only, funding_gross, net


def write_result(out: Path, label: str, price_only, funding_gross, net) -> dict:
    target = out / label
    target.mkdir(parents=True, exist_ok=True)
    price_only.trades.to_csv(target / "price_only_trades.csv", index=False)
    funding_gross.trades.to_csv(target / "funding_gross_trades.csv", index=False)
    net.trades.to_csv(target / "net_trades.csv", index=False)
    net.equity_curve.to_csv(target / "net_equity_curve.csv", index=False)
    net.signals.to_csv(target / "signals.csv", index=False)
    payload = {
        "price_only": extended_metrics(price_only.metrics, price_only.trades),
        "funding_gross": extended_metrics(funding_gross.metrics, funding_gross.trades),
        "net": extended_metrics(net.metrics, net.trades),
    }
    (target / "metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def profit_factor(values: pd.Series) -> float | None:
    if values.empty:
        return None
    wins = float(values[values > 0].sum())
    losses = float(-values[values < 0].sum())
    if losses == 0:
        return None if wins == 0 else math.inf
    return wins / losses


def build_regimes(data: pd.DataFrame) -> pd.DataFrame:
    out = data[["timestamp", "close", "fam_known_funding_rate"]].copy().sort_values("timestamp").reset_index(drop=True)
    trend_7d = out["close"] / out["close"].shift(168) - 1.0
    out["trend_regime"] = np.select(
        [trend_7d >= 0.05, trend_7d <= -0.05],
        ["BULL", "BEAR"],
        default="SIDEWAYS",
    )
    out.loc[trend_7d.isna(), "trend_regime"] = "UNCLASSIFIED"

    log_ret = np.log(out["close"] / out["close"].shift(1))
    rv24 = log_ret.rolling(24, min_periods=24).std(ddof=0) * np.sqrt(24)
    rv_ref = rv24.shift(1).rolling(30 * 24, min_periods=7 * 24).median()
    out["vol_regime"] = np.where(rv24 > rv_ref, "HIGH_VOL", "LOW_VOL")
    out.loc[rv_ref.isna(), "vol_regime"] = "UNCLASSIFIED"

    rate = out["fam_known_funding_rate"]
    out["funding_regime"] = np.select(
        [rate >= FundingCarryV1Config().entry_funding_rate, rate > 0, rate < 0],
        ["HIGH_POSITIVE_FUNDING", "MILD_POSITIVE_FUNDING", "NEGATIVE_FUNDING"],
        default="ZERO_FUNDING",
    )
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
    for col in ["trend_regime", "vol_regime", "funding_regime"]:
        for value, g in merged.groupby(col, dropna=False):
            rows.append({
                "dimension": col,
                "regime": str(value),
                "trades": int(len(g)),
                "net_pnl": float(g["net_pnl"].sum()),
                "price_pnl": float(g["pre_cost_gross_pnl"].sum()),
                "win_rate": float((g["net_pnl"] > 0).mean()),
                "profit_factor": profit_factor(g["net_pnl"]),
                "fee_cost": float(g["fee_cost"].sum()),
                "slippage_cost": float(g["slippage_cost"].sum()),
                "funding_income": float(g["funding_income"].sum()),
                "funding_cost": float(g["funding_cost"].sum()),
                "funding_net": float(g["funding_income"].sum() - g["funding_cost"].sum()),
                "average_holding_hours": float(g["holding_hours"].mean()),
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


def _signal_hours(signals: pd.DataFrame) -> set[pd.Timestamp]:
    if signals.empty:
        return set()
    entries = signals[signals["signal"].isin(["LONG", "SHORT"])].copy()
    if entries.empty:
        return set()
    return set(pd.to_datetime(entries["timestamp"], utc=True).dt.floor("h"))


def _overlap_row(name: str, primary: set[pd.Timestamp], other: set[pd.Timestamp]) -> dict:
    inter = primary & other
    union = primary | other
    return {
        "other_strategy": name,
        "fam_entry_signals": len(primary),
        "other_entry_signals": len(other),
        "intersection": len(inter),
        "fam_overlap_rate": len(inter) / len(primary) if primary else 0.0,
        "jaccard": len(inter) / len(union) if union else 0.0,
    }


def common_28d_overlap(long_data: pd.DataFrame, funding_history: pd.DataFrame, symbol: str) -> pd.DataFrame:
    start = pd.Timestamp("2026-06-03T00:00:00Z")
    end = pd.Timestamp("2026-06-30T23:00:00Z")
    fam_sample = _slice(long_data, start, end)
    fam = BacktestEngine(NET_CONFIG).run(fam_sample, FundingAlignedMomentumV1(direction_mode="LONG_ONLY"))
    fam_hours = _signal_hours(fam.signals)

    compression_data = add_precomputed_compression_features(long_data.copy())
    compression_sample = _slice(compression_data, start, end)
    compression = BacktestEngine(NET_CONFIG).run(compression_sample, VolatilityCompressionBreakoutV1(direction_mode="SHORT_ONLY"))
    compression_hours = _signal_hours(compression.signals)

    session = requests.Session()
    session.trust_env = False
    start_day = date(2026, 6, 3)
    end_day = date(2026, 6, 30)
    ohlcv = fetch_vision_ohlcv(symbol, start_day, end_day, session)
    funding = fetch_vision_funding(symbol, start_day - timedelta(days=8), end_day, session)
    oi = fetch_vision_oi(symbol, start_day, end_day, session)
    short_data = build_research_dataset(ohlcv, funding, oi, symbol)
    engine = BacktestEngine(NET_CONFIG)
    funding_reversal_hours = _signal_hours(engine.run(short_data, FundingExtremeReversalV1()).signals)
    oi_momentum_hours = _signal_hours(engine.run(short_data, OIMomentumV1()).signals)

    carry_events = funding_history[
        (funding_history["timestamp"] >= start)
        & (funding_history["timestamp"] <= end)
        & (funding_history["funding_rate"] >= FundingCarryV1Config().entry_funding_rate)
    ]
    carry_hours = set(pd.to_datetime(carry_events["timestamp"], utc=True).dt.floor("h"))

    return pd.DataFrame([
        _overlap_row("funding_extreme_reversal_v1", fam_hours, funding_reversal_hours),
        _overlap_row("oi_momentum_v1", fam_hours, oi_momentum_hours),
        _overlap_row("funding_carry_v1_entry_candidates", fam_hours, carry_hours),
        _overlap_row("volatility_compression_breakout_v1", fam_hours, compression_hours),
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description="Frozen Funding-Aligned Momentum v1 backtest; research only")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start-month", default="2019-09")
    parser.add_argument("--end-month", default="2026-06")
    parser.add_argument("--evaluation-start", default="2020-01-01T00:00:00Z")
    parser.add_argument("--output-dir", default="data/arena/funding_aligned_momentum_v1")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    hourly = load_hourly_futures(args.symbol, args.start_month, args.end_month, args.workers)
    funding = fetch_funding(args.symbol, args.start_month, args.end_month)
    data = prepare_data(hourly, funding)
    regimes = build_regimes(data)

    eval_start = pd.Timestamp(args.evaluation_start)
    eval_end = data["timestamp"].max()
    eval_data = _slice(data, eval_start, eval_end)
    splits = chronological_split(eval_data)
    oos = next(s for s in splits if s.name == "oos")

    # Frozen v1 direction was selected before Final OOS from relationship analysis: LONG_ONLY.
    price_full, funding_full, net_full = run_three(data, eval_start, eval_end, "LONG_ONLY")
    full_metrics = write_result(out, "full_2020_2026", price_full, funding_full, net_full)

    split_rows = []
    for split in splits:
        price, funding_gross, net = run_three(data, split.start, split.end, "LONG_ONLY")
        payload = write_result(out, split.name, price, funding_gross, net)
        split_rows.append({
            "split": split.name,
            "start": split.start.isoformat(),
            "end": split.end.isoformat(),
            "price_only_return": payload["price_only"].get("total_return"),
            "funding_gross_return": payload["funding_gross"].get("total_return"),
            "net_return": payload["net"].get("total_return"),
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
        price, funding_gross, net = run_three(data, start, end, "LONG_ONLY")
        yearly_rows.append({
            "year": year,
            "period_end": end.isoformat(),
            "price_only_return": price.metrics.get("total_return"),
            "funding_gross_return": funding_gross.metrics.get("total_return"),
            "net_return": net.metrics.get("total_return"),
            "annualized_return": net.metrics.get("annualized_return"),
            "maximum_drawdown": net.metrics.get("maximum_drawdown"),
            "sharpe": net.metrics.get("sharpe"),
            "sortino": net.metrics.get("sortino"),
            "profit_factor": net.metrics.get("profit_factor"),
            "win_rate": net.metrics.get("win_rate"),
            "number_of_trades": net.metrics.get("number_of_trades"),
            "fee_cost": net.metrics.get("fee_cost"),
            "slippage_cost": net.metrics.get("slippage_cost"),
            "funding_income": net.metrics.get("funding_income"),
            "funding_cost": net.metrics.get("funding_cost"),
            "average_holding_hours": net.metrics.get("average_holding_hours"),
        })
    yearly = pd.DataFrame(yearly_rows)
    yearly.to_csv(out / "yearly_performance.csv", index=False)

    recent_start = pd.Timestamp("2024-01-01T00:00:00Z")
    price_recent, funding_recent, net_recent = run_three(data, recent_start, eval_end, "LONG_ONLY")
    recent_metrics = write_result(out, "recent_2024_2026", price_recent, funding_recent, net_recent)

    direction_rows = []
    for mode in ["SHORT_ONLY", "LONG_ONLY", "BOTH"]:
        for label, start, end in [
            ("full_2020_2026", eval_start, eval_end),
            ("recent_2024_2026", recent_start, eval_end),
            ("final_oos", oos.start, oos.end),
        ]:
            price, funding_gross, net = run_three(data, start, end, mode)
            direction_rows.append({
                "direction_mode": mode,
                "period": label,
                "price_only_return": price.metrics.get("total_return"),
                "funding_gross_return": funding_gross.metrics.get("total_return"),
                "net_return": net.metrics.get("total_return"),
                "maximum_drawdown": net.metrics.get("maximum_drawdown"),
                "sharpe": net.metrics.get("sharpe"),
                "profit_factor": net.metrics.get("profit_factor"),
                "trades": net.metrics.get("number_of_trades"),
                "funding_income": net.metrics.get("funding_income"),
                "funding_cost": net.metrics.get("funding_cost"),
            })
    pd.DataFrame(direction_rows).to_csv(out / "direction_comparison.csv", index=False)

    attribution = trade_attribution(net_full.trades, regimes)
    attribution.to_csv(out / "regime_attribution.csv", index=False)
    overlap = common_28d_overlap(data, funding, args.symbol)
    overlap.to_csv(out / "common_28d_signal_overlap.csv", index=False)

    summary = {
        "strategy": "funding_aligned_momentum",
        "version": "v1",
        "rule": {
            "momentum_hours": 168,
            "direction_mode": "LONG_ONLY",
            "entry": "7-day price momentum > 0 AND latest actually observed funding payment rate < 0",
            "exit": "exit when either 7-day momentum <= 0 OR latest actually observed funding >= 0",
            "execution": "signal at hourly candle close, execute next hourly open via common engine",
            "funding_threshold_optimization": False,
            "momentum_parameter_optimization": False,
        },
        "data": {
            "start": eval_data["timestamp"].min().isoformat(),
            "end": eval_data["timestamp"].max().isoformat(),
            "hourly_rows": int(len(eval_data)),
            "funding_events": int(len(funding)),
            "source": "Binance Vision USD-M official public archives",
        },
        "lookahead_policy": "Signal uses only the latest funding payment timestamp <= candle decision timestamp; actual future funding payments affect PnL only after entry.",
        "full_2020_2026": full_metrics,
        "recent_2024_2026": recent_metrics,
        "splits": split_rows,
        "concentration": concentration_stats(net_full.trades),
        "live_trading": False,
        "actual_orders": False,
        "notes": [
            "v1 LONG_ONLY and 168h momentum were frozen from pre-OOS relationship analysis before Final OOS backtest.",
            "Price-only removes funding, fees and slippage; Funding-gross adds actual archived funding; Net additionally applies common 5bp taker fee and 5bp slippage per side.",
            "Yearly runs use the same initial equity independently and precomputed past-only features.",
            "Common 28-day overlap is timestamp-hour overlap because OI has shorter historical coverage.",
            "Existing strategy parameters and Funding Carry +2bp rule are unchanged.",
        ],
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
