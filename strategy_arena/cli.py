from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from strategy_arena.backtest import BacktestConfig, BacktestEngine, buy_and_hold_metrics, chronological_split, save_comparison, save_result
from strategy_arena.data import BinanceHistoricalClient, build_research_dataset, save_parquet, validate_research_dataset
from strategy_arena.strategies import FundingExtremeReversalV1, OIDivergenceV1, OIMomentumV1


def completed_utc_window(days: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    now = pd.Timestamp(datetime.now(timezone.utc))
    end = now.floor("D") - pd.Timedelta(milliseconds=1)
    start = (end + pd.Timedelta(milliseconds=1)) - pd.Timedelta(days=days)
    return start, end


def main() -> None:
    parser = argparse.ArgumentParser(description="BitSwipe Strategy Arena backtest MVP (no live trading)")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--days", type=int, default=28)
    parser.add_argument("--data-dir", default="data/arena")
    parser.add_argument("--output-dir", default="data/arena/results")
    parser.add_argument("--leverage", type=float, default=2.0)
    args = parser.parse_args()

    start, end = completed_utc_window(args.days)
    client = BinanceHistoricalClient()
    ohlcv = client.fetch_ohlcv(args.symbol, "1h", start, end)
    funding = client.fetch_funding_history(args.symbol, start - pd.Timedelta(days=8), end)
    oi = client.fetch_open_interest_history(args.symbol, "1h", start, end)

    root = Path(args.data_dir) / "raw" / args.symbol / "1h"
    save_parquet(ohlcv, root / "ohlcv.parquet")
    save_parquet(funding, root / "funding.parquet")
    save_parquet(oi, root / "open_interest.parquet")

    dataset = build_research_dataset(ohlcv, funding, oi, args.symbol)
    report = validate_research_dataset(dataset)
    save_parquet(dataset, Path(args.data_dir) / "datasets" / f"{args.symbol}_1h_{start.date()}_{end.date()}.parquet")

    engine = BacktestEngine(BacktestConfig(leverage=args.leverage))
    strategies = [FundingExtremeReversalV1(), OIMomentumV1(), OIDivergenceV1()]
    comparison: list[dict] = []
    for split in chronological_split(dataset):
        split_data = dataset[(dataset["timestamp"] >= split.start) & (dataset["timestamp"] <= split.end)]
        benchmark = buy_and_hold_metrics(split_data)
        comparison.append({"strategy": "BTC_BUY_HOLD", "version": "benchmark", "split": split.name, **benchmark})
        for strategy in strategies:
            result = engine.run(dataset, strategy, split.start, split.end)
            save_result(result, args.output_dir, split.name)
            comparison.append({"strategy": result.strategy_name, "version": result.strategy_version, "split": split.name, **result.metrics})

    comparison_path = save_comparison(comparison, args.output_dir)
    print(json.dumps({
        "symbol": args.symbol,
        "period_utc": [start.isoformat(), end.isoformat()],
        "validation_warnings": report.warnings,
        "comparison": str(comparison_path),
        "live_trading": False,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
