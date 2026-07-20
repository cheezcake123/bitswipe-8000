from __future__ import annotations

import argparse
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import requests

FUTURES_MONTHLY = "https://data.binance.vision/data/futures/um/monthly/klines"
KLINE_COLUMNS = [
    "timestamp", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
]
HORIZONS = (1, 3, 7, 14)


@dataclass(frozen=True)
class TrendRelationshipConfig:
    symbol: str = "BTCUSDT"
    start_month: str = "2020-01"
    end_month: str = "2026-06"
    breakout_windows: tuple[int, ...] = (20, 55)
    atr_window: int = 14
    volatility_reference_days: int = 252
    volume_window: int = 20
    regime_lookback_days: int = 90
    bull_threshold: float = 0.10
    bear_threshold: float = -0.10


def _month_range(start_month: str, end_month: str) -> list[str]:
    return [str(p) for p in pd.period_range(pd.Period(start_month, freq="M"), pd.Period(end_month, freq="M"), freq="M")]


def _epoch_unit(series: pd.Series) -> str:
    values = pd.to_numeric(series, errors="raise")
    return "us" if values.abs().median() > 1e14 else "ms"


def _read_kline_zip(content: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        csvs = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csvs:
            raise RuntimeError("No CSV in Binance archive")
        with zf.open(csvs[0]) as fh:
            raw = pd.read_csv(fh)
        aliases = {
            "open_time": "timestamp",
            "quote_asset_volume": "quote_volume",
            "number_of_trades": "trades",
            "num_trades": "trades",
            "taker_buy_base_asset_volume": "taker_buy_base",
            "taker_buy_volume": "taker_buy_base",
            "taker_buy_quote_asset_volume": "taker_buy_quote",
            "taker_buy_quote_volume": "taker_buy_quote",
        }
        raw = raw.rename(columns={k: v for k, v in aliases.items() if k in raw.columns})
        if "timestamp" not in raw.columns:
            with zf.open(csvs[0]) as fh:
                raw = pd.read_csv(fh, header=None, names=KLINE_COLUMNS)
    for col in KLINE_COLUMNS:
        if col not in raw.columns:
            raw[col] = pd.NA
    raw["timestamp"] = pd.to_datetime(pd.to_numeric(raw["timestamp"], errors="raise"), unit=_epoch_unit(raw["timestamp"]), utc=True)
    raw["close_time"] = pd.to_datetime(pd.to_numeric(raw["close_time"], errors="raise"), unit=_epoch_unit(raw["close_time"]), utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        raw[col] = pd.to_numeric(raw[col], errors="raise")
    return raw[KLINE_COLUMNS].sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def _download_month(symbol: str, month: str, timeout: int = 60) -> tuple[str, pd.DataFrame | None]:
    url = f"{FUTURES_MONTHLY}/{symbol}/1d/{symbol}-1d-{month}.zip"
    response = requests.get(url, timeout=timeout)
    if response.status_code == 404:
        return month, None
    response.raise_for_status()
    return month, _read_kline_zip(response.content)


def load_daily_futures(config: TrendRelationshipConfig, workers: int = 6) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_download_month, config.symbol, m) for m in _month_range(config.start_month, config.end_month)]
        for future in as_completed(futures):
            _, frame = future.result()
            if frame is not None:
                frames.append(frame)
    if not frames:
        raise RuntimeError("No BTCUSDT futures daily archives downloaded")
    return pd.concat(frames, ignore_index=True).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def add_common_features(df: pd.DataFrame, config: TrendRelationshipConfig) -> pd.DataFrame:
    out = df.copy()
    prev_close = out["close"].shift(1)
    tr = pd.concat([
        out["high"] - out["low"],
        (out["high"] - prev_close).abs(),
        (out["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    out["atr14"] = tr.rolling(config.atr_window, min_periods=config.atr_window).mean().shift(1)
    out["atr_pct"] = out["atr14"] / prev_close
    vol_ref = out["atr_pct"].rolling(config.volatility_reference_days, min_periods=126).median().shift(1)
    out["vol_regime"] = np.where(out["atr_pct"] > vol_ref, "HIGH_VOL", "LOW_VOL")
    out.loc[vol_ref.isna(), "vol_regime"] = "UNCLASSIFIED"

    volume_avg = out["volume"].rolling(config.volume_window, min_periods=config.volume_window).mean().shift(1)
    out["volume_ratio"] = out["volume"] / volume_avg
    out["volume_state"] = np.where(out["volume"] > volume_avg, "ABOVE_AVG", "BELOW_AVG")
    out.loc[volume_avg.isna(), "volume_state"] = "UNCLASSIFIED"

    past_ret = out["close"] / out["close"].shift(config.regime_lookback_days) - 1.0
    out["trend_regime"] = np.select(
        [past_ret >= config.bull_threshold, past_ret <= config.bear_threshold],
        ["BULL", "BEAR"],
        default="SIDEWAYS",
    )
    out.loc[past_ret.isna(), "trend_regime"] = "UNCLASSIFIED"
    for h in HORIZONS:
        out[f"future_return_{h}d"] = out["close"].shift(-h) / out["close"] - 1.0
    return out


def breakout_events(df: pd.DataFrame, window: int) -> pd.DataFrame:
    out = df.copy()
    upper = out["high"].rolling(window, min_periods=window).max().shift(1)
    lower = out["low"].rolling(window, min_periods=window).min().shift(1)
    up = out["close"] > upper
    down = out["close"] < lower
    up_event = up & ~up.shift(1, fill_value=False)
    down_event = down & ~down.shift(1, fill_value=False)
    events = out.loc[up_event | down_event].copy()
    events["side"] = np.where(up_event.loc[events.index], "UP", "DOWN")
    events["window"] = window
    for h in HORIZONS:
        raw = events[f"future_return_{h}d"]
        events[f"signed_future_return_{h}d"] = np.where(events["side"] == "UP", raw, -raw)
    return events


def summarize_group(group: pd.DataFrame, group_name: str, group_value: str, window: int) -> list[dict]:
    rows: list[dict] = []
    for side, side_group in group.groupby("side"):
        for h in HORIZONS:
            values = side_group[f"signed_future_return_{h}d"].dropna()
            rows.append({
                "window": window,
                "group": group_name,
                "value": group_value,
                "side": side,
                "horizon_days": h,
                "events": int(len(values)),
                "mean_signed_return": float(values.mean()) if len(values) else None,
                "median_signed_return": float(values.median()) if len(values) else None,
                "trend_follow_hit_rate": float((values > 0).mean()) if len(values) else None,
            })
    return rows


def run_relationship_analysis(df: pd.DataFrame, config: TrendRelationshipConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    summary_rows: list[dict] = []
    event_frames: list[pd.DataFrame] = []
    for window in config.breakout_windows:
        events = breakout_events(df, window)
        event_frames.append(events)
        summary_rows.extend(summarize_group(events, "ALL", "ALL", window))
        for col in ["vol_regime", "volume_state", "trend_regime"]:
            for value, group in events.groupby(col):
                if value == "UNCLASSIFIED":
                    continue
                summary_rows.extend(summarize_group(group, col, str(value), window))
        for year, group in events.groupby(events["timestamp"].dt.year):
            summary_rows.extend(summarize_group(group, "YEAR", str(int(year)), window))
        recent = events[events["timestamp"].dt.year >= 2024]
        summary_rows.extend(summarize_group(recent, "RECENT", "2024_2026", window))
    return pd.DataFrame(summary_rows), pd.concat(event_frames, ignore_index=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fixed 20d/55d Donchian relationship research; no strategy optimization")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start-month", default="2020-01")
    parser.add_argument("--end-month", default="2026-06")
    parser.add_argument("--output-dir", default="data/arena/trend_breakout_research")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    config = TrendRelationshipConfig(symbol=args.symbol, start_month=args.start_month, end_month=args.end_month)
    data = add_common_features(load_daily_futures(config, args.workers), config)
    summary, events = run_relationship_analysis(data, config)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    data.to_parquet(out / "btc_usdt_futures_daily.parquet", index=False)
    events.to_csv(out / "breakout_events.csv", index=False)
    summary.to_csv(out / "relationship_summary.csv", index=False)
    metadata = {
        "config": asdict(config),
        "data_start": data["timestamp"].min().isoformat(),
        "data_end": data["timestamp"].max().isoformat(),
        "rows": int(len(data)),
        "windows_compared": list(config.breakout_windows),
        "optimization_performed": False,
        "note": "20d and 55d are fixed classical comparison anchors, not selected from a parameter sweep.",
    }
    with open(out / "research_metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
