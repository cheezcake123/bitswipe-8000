from __future__ import annotations

import argparse
import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from strategy_arena.archive_cli import fetch_vision_funding, fetch_vision_ohlcv, fetch_vision_oi
from strategy_arena.backtest import BacktestConfig, BacktestEngine, chronological_split
from strategy_arena.basis_execution import fetch_funding
from strategy_arena.data import build_research_dataset
from strategy_arena.strategies import FundingExtremeReversalV1, OIMomentumV1
from strategy_arena.trend_breakout_research import (
    FUTURES_MONTHLY,
    TrendRelationshipConfig,
    _month_range,
    _read_kline_zip,
    add_common_features,
)
from strategy_arena.trend_breakout_v1 import VolatilityAdjustedTrendBreakoutV1


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


def _download_hourly_month(symbol: str, month: str, timeout: int = 60) -> tuple[str, pd.DataFrame | None]:
    url = f"{FUTURES_MONTHLY}/{symbol}/1h/{symbol}-1h-{month}.zip"
    response = requests.get(url, timeout=timeout)
    if response.status_code == 404:
        return month, None
    response.raise_for_status()
    return month, _read_kline_zip(response.content)


def load_hourly_futures(symbol: str, start_month: str, end_month: str, workers: int = 6) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_download_hourly_month, symbol, month) for month in _month_range(start_month, end_month)]
        for future in as_completed(futures):
            _, frame = future.result()
            if frame is not None:
                frames.append(frame)
    if not frames:
        raise RuntimeError("No hourly futures data downloaded")
    data = pd.concat(frames, ignore_index=True).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    data["symbol"] = symbol
    data["source"] = "binance_vision_usdm"
    data["decision_timestamp"] = data["close_time"]
    return data[["timestamp", "close_time", "decision_timestamp", "symbol", "source", "open", "high", "low", "close", "volume"]]


def attach_funding(hourly: pd.DataFrame, funding: pd.DataFrame) -> pd.DataFrame:
    out = hourly.copy()
    if funding.empty:
        out["funding_payment_rate"] = 0.0
        return out
    f = funding.copy()
    f["hour"] = pd.to_datetime(f["timestamp"], utc=True).dt.floor("h")
    rate_by_hour = f.groupby("hour", as_index=True)["funding_rate"].sum()
    out["funding_payment_rate"] = out["timestamp"].map(rate_by_hour).fillna(0.0).astype(float)
    return out


def daily_regimes(hourly: pd.DataFrame) -> pd.DataFrame:
    daily = (
        hourly.set_index("timestamp")
        .resample("1D")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )
    cfg = TrendRelationshipConfig()
    features = add_common_features(daily, cfg)
    return features[["timestamp", "vol_regime", "trend_regime", "atr_pct", "volume_state"]]


def _safe_number(value):
    if isinstance(value, (float, np.floating)) and (math.isnan(float(value)) or math.isinf(float(value))):
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
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


def trade_attribution(trades: pd.DataFrame, regimes: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    t = trades.copy()
    t["entry_day"] = pd.to_datetime(t["entry_time"], utc=True).dt.floor("D")
    merged = t.merge(regimes.rename(columns={"timestamp": "entry_day"}), on="entry_day", how="left")
    rows = []
    for group_col in ["trend_regime", "vol_regime"]:
        for value, group in merged.groupby(group_col, dropna=False):
            rows.append({
                "group": group_col,
                "value": str(value),
                "trades": int(len(group)),
                "net_pnl": float(group["net_pnl"].sum()),
                "pre_cost_gross_pnl": float(group["pre_cost_gross_pnl"].sum()),
                "win_rate": float((group["net_pnl"] > 0).mean()),
                "profit_factor": profit_factor(group["net_pnl"]),
                "fee_cost": float(group["fee_cost"].sum()),
                "slippage_cost": float(group["slippage_cost"].sum()),
                "funding_net": float(group["funding_income"].sum() - group["funding_cost"].sum()),
                "average_holding_hours": float(group["holding_hours"].mean()),
            })
    return pd.DataFrame(rows)


def run_pair(data: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> tuple[object, object]:
    strategy = VolatilityAdjustedTrendBreakoutV1()
    net = BacktestEngine(NET_CONFIG).run(data, strategy, start, end)
    no_funding = data.copy()
    no_funding["funding_payment_rate"] = 0.0
    gross = BacktestEngine(GROSS_CONFIG).run(no_funding, VolatilityAdjustedTrendBreakoutV1(), start, end)
    return gross, net


def write_result_set(out: Path, label: str, gross, net) -> None:
    target = out / label
    target.mkdir(parents=True, exist_ok=True)
    net.trades.to_csv(target / "net_trades.csv", index=False)
    net.equity_curve.to_csv(target / "net_equity_curve.csv", index=False)
    gross.equity_curve.to_csv(target / "gross_equity_curve.csv", index=False)
    net.signals.to_csv(target / "signals.csv", index=False)
    payload = {"gross": safe_dict(gross.metrics), "net": safe_dict(net.metrics)}
    (target / "metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def concentration_stats(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"top1_share_of_positive_pnl": None, "top3_share_of_positive_pnl": None}
    positive = trades.loc[trades["net_pnl"] > 0, "net_pnl"].sort_values(ascending=False)
    total = float(positive.sum())
    if total <= 0:
        return {"top1_share_of_positive_pnl": None, "top3_share_of_positive_pnl": None}
    return {
        "top1_share_of_positive_pnl": float(positive.head(1).sum() / total),
        "top3_share_of_positive_pnl": float(positive.head(3).sum() / total),
    }


def common_28d_comparison(long_hourly: pd.DataFrame, symbol: str) -> pd.DataFrame:
    start_day = date(2026, 6, 3)
    end_day = date(2026, 6, 30)
    session = requests.Session()
    session.trust_env = False
    ohlcv = fetch_vision_ohlcv(symbol, start_day, end_day, session)
    funding = fetch_vision_funding(symbol, start_day - timedelta(days=8), end_day, session)
    oi = fetch_vision_oi(symbol, start_day, end_day, session)
    short_data = build_research_dataset(ohlcv, funding, oi, symbol)
    start = pd.Timestamp("2026-06-03T00:00:00Z")
    end = pd.Timestamp("2026-06-30T23:00:00Z")
    rows = []
    short_engine = BacktestEngine(NET_CONFIG)
    for strategy in [FundingExtremeReversalV1(), OIMomentumV1()]:
        result = short_engine.run(short_data, strategy, start, end)
        rows.append({"strategy": strategy.strategy_name, "version": strategy.strategy_version, **safe_dict(result.metrics)})
    trend = BacktestEngine(NET_CONFIG).run(long_hourly, VolatilityAdjustedTrendBreakoutV1(), start, end)
    rows.append({"strategy": trend.strategy_name, "version": trend.strategy_version, **safe_dict(trend.metrics)})
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Long-horizon fixed-rule Volatility-Adjusted Trend Breakout v1 research backtest")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start-month", default="2019-09")
    parser.add_argument("--end-month", default="2026-06")
    parser.add_argument("--evaluation-start", default="2020-01-01")
    parser.add_argument("--output-dir", default="data/arena/trend_breakout_v1")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    hourly = load_hourly_futures(args.symbol, args.start_month, args.end_month, args.workers)
    funding = fetch_funding(args.symbol, args.start_month, args.end_month)
    data = attach_funding(hourly, funding)
    regimes = daily_regimes(hourly)

    eval_start = pd.Timestamp(args.evaluation_start, tz="UTC")
    eval_data = data[data["timestamp"] >= eval_start]
    eval_end = data["timestamp"].max()
    gross_full, net_full = run_pair(data, eval_start, eval_end)
    write_result_set(out, "full_2020_2026", gross_full, net_full)

    split_rows = []
    for split in chronological_split(eval_data):
        gross, net = run_pair(data, split.start, split.end)
        write_result_set(out, split.name, gross, net)
        split_rows.append({
            "split": split.name,
            "start": split.start.isoformat(),
            "end": split.end.isoformat(),
            "gross_total_return": gross.metrics.get("total_return"),
            "net_total_return": net.metrics.get("total_return"),
            "net_mdd": net.metrics.get("maximum_drawdown"),
            "net_sharpe": net.metrics.get("sharpe"),
            "net_profit_factor": net.metrics.get("profit_factor"),
            "trades": net.metrics.get("number_of_trades"),
        })
    pd.DataFrame(split_rows).to_csv(out / "split_performance.csv", index=False)

    yearly_rows = []
    for year in range(2020, 2027):
        start = pd.Timestamp(f"{year}-01-01T00:00:00Z")
        year_end = min(pd.Timestamp(f"{year}-12-31T23:00:00Z"), eval_end)
        if start > eval_end:
            continue
        gross, net = run_pair(data, start, year_end)
        yearly_rows.append({
            "year": year,
            "period_end": year_end.isoformat(),
            "gross_total_return": gross.metrics.get("total_return"),
            "net_total_return": net.metrics.get("total_return"),
            "cagr_or_annualized": net.metrics.get("annualized_return"),
            "maximum_drawdown": net.metrics.get("maximum_drawdown"),
            "sharpe": net.metrics.get("sharpe"),
            "sortino": net.metrics.get("sortino"),
            "profit_factor": net.metrics.get("profit_factor"),
            "win_rate": net.metrics.get("win_rate"),
            "number_of_trades": net.metrics.get("number_of_trades"),
            "fee_cost": net.metrics.get("fee_cost"),
            "slippage_cost": net.metrics.get("slippage_cost"),
            "funding_cost": net.metrics.get("funding_cost"),
            "funding_income": net.metrics.get("funding_income"),
            "average_holding_hours": net.metrics.get("average_holding_hours"),
        })
    yearly = pd.DataFrame(yearly_rows)
    yearly.to_csv(out / "yearly_performance.csv", index=False)

    recent_start = pd.Timestamp("2024-01-01T00:00:00Z")
    gross_recent, net_recent = run_pair(data, recent_start, eval_end)
    write_result_set(out, "recent_2024_2026", gross_recent, net_recent)

    attribution = trade_attribution(net_full.trades, regimes)
    attribution.to_csv(out / "regime_attribution.csv", index=False)
    comparison = common_28d_comparison(data, args.symbol)
    comparison.to_csv(out / "common_28d_comparison.csv", index=False)

    expected_hours = int((hourly["timestamp"].max() - hourly["timestamp"].min()) / pd.Timedelta(hours=1)) + 1
    full_summary = {
        "strategy": "volatility_adjusted_trend_breakout",
        "version": "v1",
        "rule": asdict(VolatilityAdjustedTrendBreakoutV1()),
        "parameter_optimization": False,
        "data": {
            "start": hourly["timestamp"].min().isoformat(),
            "end": hourly["timestamp"].max().isoformat(),
            "hourly_rows": int(len(hourly)),
            "expected_hours": expected_hours,
            "missing_hours": int(expected_hours - len(hourly)),
            "funding_events": int(len(funding)),
            "source": "Binance Vision USD-M official public archives",
        },
        "full_2020_2026": {
            "gross": safe_dict(gross_full.metrics),
            "net": safe_dict(net_full.metrics),
            "cost_difference_return": float(gross_full.metrics["total_return"] - net_full.metrics["total_return"]),
            "concentration": concentration_stats(net_full.trades),
        },
        "recent_2024_2026": {"gross": safe_dict(gross_recent.metrics), "net": safe_dict(net_recent.metrics)},
        "splits": split_rows,
        "live_trading": False,
        "actual_orders": False,
        "notes": [
            "Signals are created only after the final UTC hourly candle closes and execute at the next hourly open.",
            "Position size is capped at 100% equity before leverage and reduced when current ATR% exceeds its trailing median; leverage is capped at 2x.",
            "Gross run removes fee, slippage and funding; Net run uses the common 5bp taker fee, 5bp slippage and actual archived funding events.",
            "Regime results are trade-entry attribution, not a regime-filtered optimized strategy.",
        ],
    }
    (out / "summary.json").write_text(json.dumps(full_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(full_summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
