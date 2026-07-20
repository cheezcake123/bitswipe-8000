from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from strategy_arena.backtest import BacktestEngine, chronological_split
from strategy_arena.basis_execution import fetch_funding
from strategy_arena.funding_carry_v1 import FundingCarryV1Config
from strategy_arena.trend_breakout_backtest import attach_funding, load_hourly_futures
from strategy_arena.volatility_compression_backtest import (
    NET_CONFIG,
    add_precomputed_compression_features,
    build_regime_frame,
    concentration_stats,
    enrich_trades,
    extended_metrics,
    run_pair,
    trade_attribution,
    write_result,
)
from strategy_arena.volatility_compression_v1 import VolatilityCompressionBreakoutV1


def _slice(data: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    return data[(data["timestamp"] >= start) & (data["timestamp"] <= end)].reset_index(drop=True)


def run_pair_window(data: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, mode: str = "SHORT_ONLY"):
    window = _slice(data, start, end)
    return run_pair(window, start, end, mode)


def run_net_window(data: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, mode: str):
    window = _slice(data, start, end)
    result = BacktestEngine(NET_CONFIG).run(window, VolatilityCompressionBreakoutV1(direction_mode=mode), start, end)
    result.trades = enrich_trades(result.trades, window)
    return result


def main() -> None:
    p = argparse.ArgumentParser(description="Fast fixed-rule Volatility Compression Breakout v1 evaluator")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--start-month", default="2019-09")
    p.add_argument("--end-month", default="2026-06")
    p.add_argument("--evaluation-start", default="2020-01-01T00:00:00Z")
    p.add_argument("--output-dir", default="data/arena/volatility_compression_v1")
    p.add_argument("--workers", type=int, default=6)
    args = p.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    raw = load_hourly_futures(args.symbol, args.start_month, args.end_month, args.workers)
    featured = add_precomputed_compression_features(raw)
    funding = fetch_funding(args.symbol, args.start_month, args.end_month)
    data = attach_funding(featured, funding)
    regimes = build_regime_frame(raw, funding)

    eval_start = pd.Timestamp(args.evaluation_start)
    eval_end = data["timestamp"].max()
    eval_data = _slice(data, eval_start, eval_end)
    splits = chronological_split(eval_data)
    oos = next(x for x in splits if x.name == "oos")

    gross_full, net_full = run_pair_window(data, eval_start, eval_end, "SHORT_ONLY")
    full_metrics = write_result(out, "full_2020_2026", gross_full, net_full)

    split_rows, split_payload = [], {}
    for split in splits:
        gross, net = run_pair_window(data, split.start, split.end, "SHORT_ONLY")
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
        net = run_net_window(data, start, end, "SHORT_ONLY")
        yearly_rows.append({"year": year, "period_end": end.isoformat(), **{f"net_{k}": v for k, v in extended_metrics(net.metrics, net.trades).items()}})
    yearly = pd.DataFrame(yearly_rows)
    yearly.to_csv(out / "yearly_performance.csv", index=False)

    recent_start = pd.Timestamp("2024-01-01T00:00:00Z")
    gross_recent, net_recent = run_pair_window(data, recent_start, eval_end, "SHORT_ONLY")
    recent_metrics = write_result(out, "recent_2024_2026", gross_recent, net_recent)

    # SHORT_ONLY rows reuse the frozen-v1 runs; LONG_ONLY and BOTH are net-only diagnostics.
    direction_rows = []
    frozen_periods = {
        "full_2020_2026": (eval_start, eval_end, net_full),
        "recent_2024_2026": (recent_start, eval_end, net_recent),
    }
    oos_net = run_net_window(data, oos.start, oos.end, "SHORT_ONLY")
    frozen_periods["final_oos"] = (oos.start, oos.end, oos_net)
    for period, (start, end, result) in frozen_periods.items():
        m = extended_metrics(result.metrics, result.trades)
        direction_rows.append({"direction_mode": "SHORT_ONLY", "period": period, "start": start.isoformat(), "end": end.isoformat(), **m})
    for mode in ("LONG_ONLY", "BOTH"):
        for period, start, end in (
            ("full_2020_2026", eval_start, eval_end),
            ("recent_2024_2026", recent_start, eval_end),
            ("final_oos", oos.start, oos.end),
        ):
            net = run_net_window(data, start, end, mode)
            direction_rows.append({"direction_mode": mode, "period": period, "start": start.isoformat(), "end": end.isoformat(), **extended_metrics(net.metrics, net.trades)})
    pd.DataFrame(direction_rows).to_csv(out / "direction_comparison.csv", index=False)

    attribution = trade_attribution(net_full.trades, regimes)
    attribution.to_csv(out / "regime_attribution.csv", index=False)

    expected = int((raw["timestamp"].max() - raw["timestamp"].min()) / pd.Timedelta(hours=1)) + 1
    summary = {
        "strategy": "volatility_compression_breakout",
        "version": "v1",
        "frozen_rule": asdict(VolatilityCompressionBreakoutV1()),
        "selected_direction": "SHORT_ONLY",
        "selection_basis": "Fixed relationship study chose ATR% compression over BB Width; recent 2024-2026 SHORT retained positive 24h directional edge while LONG weakened, and recent SHORT 72h edge reversed, so maximum holding is fixed at 24h.",
        "parameter_optimization": False,
        "data": {
            "start": raw["timestamp"].min().isoformat(),
            "end": raw["timestamp"].max().isoformat(),
            "hourly_rows": int(len(raw)),
            "expected_hours": expected,
            "missing_hours": int(expected - len(raw)),
            "funding_events": int(len(funding)),
            "source": "Binance Vision USD-M official public archives",
        },
        "full_2020_2026": full_metrics,
        "recent_2024_2026": recent_metrics,
        "final_oos": split_payload["oos"],
        "splits": split_rows,
        "concentration": concentration_stats(net_full.trades),
        "risk_notes": [
            "Frozen v1 is SHORT-only. LONG-only and BOTH are diagnostic comparison runs with identical entry/exit rules.",
            "Signals use past-only ATR compression and prior 24h range; common engine executes at next hourly open.",
            "Exit is original-range re-entry or fixed 24h time stop, whichever occurs first.",
            "Gross removes fee, slippage and funding; Net uses common 5bp taker fee, 5bp slippage, 2x leverage and archived funding.",
            "HIGH_VOLATILITY_EXPANSION attribution is retrospective only and never a signal/filter.",
            "Historical High-OI regime is not claimed because free official OI history is insufficient for 2020-2026.",
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
