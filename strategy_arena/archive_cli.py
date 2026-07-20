from __future__ import annotations

import argparse
import io
import json
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

from strategy_arena.backtest import BacktestConfig, BacktestEngine, buy_and_hold_metrics, chronological_split, save_comparison, save_result
from strategy_arena.data import build_research_dataset, save_parquet, validate_research_dataset, validate_source_table
from strategy_arena.reporting import save_visual_report
from strategy_arena.strategies import FundingExtremeReversalV1, OIDivergenceV1, OIMomentumV1

BASE = "https://data.binance.vision/data/futures/um/daily"
KLINE_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]


def _download_archive_bytes(url: str, session: requests.Session) -> bytes:
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return response.content


def _csv_from_zip(content: bytes, *, header="infer", names=None) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            raise RuntimeError("No CSV in Binance Vision archive")
        with zf.open(csv_names[0]) as fh:
            return pd.read_csv(fh, header=header, names=names)


def _download_csv(url: str, session: requests.Session) -> pd.DataFrame:
    return _csv_from_zip(_download_archive_bytes(url, session))


def _dates(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _epoch_unit(series: pd.Series) -> str:
    numeric = pd.to_numeric(series, errors="raise")
    return "us" if numeric.abs().median() > 1e14 else "ms"


def fetch_vision_ohlcv(symbol: str, start: date, end: date, session: requests.Session) -> pd.DataFrame:
    frames = []
    for day in _dates(start, end):
        ds = day.isoformat()
        url = f"{BASE}/klines/{symbol}/1h/{symbol}-1h-{ds}.zip"
        content = _download_archive_bytes(url, session)
        raw = _csv_from_zip(content, header=None, names=KLINE_COLUMNS)
        frames.append(raw)
    df = pd.concat(frames, ignore_index=True)
    ts_unit = _epoch_unit(df["timestamp"])
    close_unit = _epoch_unit(df["close_time"])
    df["timestamp"] = pd.to_datetime(pd.to_numeric(df["timestamp"], errors="raise"), unit=ts_unit, utc=True)
    df["close_time"] = pd.to_datetime(pd.to_numeric(df["close_time"], errors="raise"), unit=close_unit, utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="raise")
    df["symbol"] = symbol
    df["source"] = "binance_vision_usdm"
    out = df[["timestamp", "close_time", "symbol", "source", "open", "high", "low", "close", "volume"]].drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    validate_source_table(out, "timestamp", "ohlcv")
    return out


def fetch_vision_funding(symbol: str, start: date, end: date, session: requests.Session) -> pd.DataFrame:
    frames = []
    missing_days = []
    for day in _dates(start, end):
        ds = day.isoformat()
        url = f"{BASE}/fundingRate/{symbol}/{symbol}-fundingRate-{ds}.zip"
        try:
            frames.append(_download_csv(url, session))
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                missing_days.append(ds)
                continue
            raise
    if not frames:
        raise RuntimeError(f"No fundingRate archives available for requested period; missing={missing_days[:5]}")
    df = pd.concat(frames, ignore_index=True)
    time_col = next((c for c in ["calc_time", "fundingTime", "funding_time", "timestamp"] if c in df.columns), None)
    rate_col = next((c for c in ["last_funding_rate", "fundingRate", "funding_rate"] if c in df.columns), None)
    if not time_col or not rate_col:
        raise RuntimeError(f"Unexpected funding columns: {list(df.columns)}")
    numeric_time = pd.to_numeric(df[time_col], errors="coerce")
    if numeric_time.notna().all():
        unit = "us" if numeric_time.abs().median() > 1e14 else "ms"
        ts = pd.to_datetime(numeric_time.astype("int64"), unit=unit, utc=True)
    else:
        ts = pd.to_datetime(df[time_col], utc=True)
    out = pd.DataFrame({
        "timestamp": ts,
        "symbol": symbol,
        "source": "binance_vision_usdm",
        "funding_rate": pd.to_numeric(df[rate_col], errors="raise"),
        "mark_price": pd.to_numeric(df["mark_price"], errors="coerce") if "mark_price" in df.columns else float("nan"),
    }).drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    validate_source_table(out, "timestamp", "funding")
    return out


def fetch_vision_oi(symbol: str, start: date, end: date, session: requests.Session) -> pd.DataFrame:
    frames = []
    for day in _dates(start, end):
        ds = day.isoformat()
        url = f"{BASE}/metrics/{symbol}/{symbol}-metrics-{ds}.zip"
        frames.append(_download_csv(url, session))
    df = pd.concat(frames, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["create_time"], utc=True)
    df["open_interest"] = pd.to_numeric(df["sum_open_interest"], errors="raise")
    df["open_interest_value"] = pd.to_numeric(df["sum_open_interest_value"], errors="coerce")
    out = df[["timestamp", "open_interest", "open_interest_value"]].copy()
    out["symbol"] = symbol
    out["source"] = "binance_vision_usdm"
    out = out[["timestamp", "symbol", "source", "open_interest", "open_interest_value"]].drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    validate_source_table(out, "timestamp", "open_interest")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="BitSwipe Strategy Arena Binance Vision E2E backtest (read-only, no live trading)")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start", default="2026-06-21")
    parser.add_argument("--end", default="2026-07-18")
    parser.add_argument("--data-dir", default="data/arena")
    parser.add_argument("--output-dir", default="data/arena/results")
    parser.add_argument("--leverage", type=float, default=2.0)
    args = parser.parse_args()

    start_day = date.fromisoformat(args.start)
    end_day = date.fromisoformat(args.end)
    funding_start = start_day - timedelta(days=8)
    session = requests.Session()
    session.trust_env = False

    ohlcv = fetch_vision_ohlcv(args.symbol, start_day, end_day, session)
    funding = fetch_vision_funding(args.symbol, funding_start, end_day, session)
    oi = fetch_vision_oi(args.symbol, start_day, end_day, session)

    root = Path(args.data_dir) / "raw" / args.symbol / "1h"
    save_parquet(ohlcv, root / "ohlcv.parquet")
    save_parquet(funding, root / "funding.parquet")
    save_parquet(oi, root / "open_interest.parquet")

    dataset = build_research_dataset(ohlcv, funding, oi, args.symbol)
    report = validate_research_dataset(dataset)
    dataset_path = Path(args.data_dir) / "datasets" / f"{args.symbol}_1h_{args.start}_{args.end}.parquet"
    save_parquet(dataset, dataset_path)

    engine = BacktestEngine(BacktestConfig(leverage=args.leverage))
    strategies = [FundingExtremeReversalV1(), OIMomentumV1(), OIDivergenceV1()]
    comparison: list[dict] = []
    split_rows = []
    for split in chronological_split(dataset):
        split_rows.append({"name": split.name, "start": split.start.isoformat(), "end": split.end.isoformat()})
        split_data = dataset[(dataset["timestamp"] >= split.start) & (dataset["timestamp"] <= split.end)]
        benchmark = buy_and_hold_metrics(split_data)
        comparison.append({"strategy": "BTC_BUY_HOLD", "version": "benchmark", "split": split.name, **benchmark})
        for strategy in strategies:
            result = engine.run(dataset, strategy, split.start, split.end)
            result_dir = Path(args.output_dir) / result.strategy_name / result.strategy_version / split.name
            save_result(result, args.output_dir, split.name)
            save_visual_report(result, result_dir)
            comparison.append({"strategy": result.strategy_name, "version": result.strategy_version, "split": split.name, **result.metrics})

    comparison_path = save_comparison(comparison, args.output_dir)
    summary = {
        "symbol": args.symbol,
        "period_utc": [ohlcv.iloc[0]["timestamp"].isoformat(), ohlcv.iloc[-1]["close_time"].isoformat()],
        "source": "Binance Vision public archives",
        "counts": {"ohlcv": len(ohlcv), "funding": len(funding), "oi": len(oi), "dataset": len(dataset)},
        "validation_warnings": report.warnings,
        "splits": split_rows,
        "comparison": str(comparison_path),
        "dataset": str(dataset_path),
        "lookahead_check": "PASS",
        "live_trading": False,
    }
    Path(args.data_dir).mkdir(parents=True, exist_ok=True)
    (Path(args.data_dir) / "e2e_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
