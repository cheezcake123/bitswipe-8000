from __future__ import annotations

import argparse
import io
import json
import math
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from strategy_arena.market_store import AppendOnlyMarketStore

SPOT_MONTHLY = "https://data.binance.vision/data/spot/monthly/klines"
FUTURES_MONTHLY = "https://data.binance.vision/data/futures/um/monthly/klines"
KLINE_COLUMNS = [
    "timestamp", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
]
MINUTES = {"1h": 60, "4h": 240, "12h": 720, "24h": 1440}


@dataclass(frozen=True)
class BasisResearchConfig:
    symbol: str = "BTCUSDT"
    start_month: str = "2019-09"
    end_month: str = "2026-06"
    z_window_minutes: int = 30 * 24 * 60
    extreme_z: float = 2.0
    reset_z: float = 1.0
    trend_lookback_minutes: int = 7 * 24 * 60
    trend_threshold: float = 0.05
    volatility_window_minutes: int = 24 * 60
    volatility_reference_minutes: int = 30 * 24 * 60


def _month_range(start_month: str, end_month: str) -> list[str]:
    start = pd.Period(start_month, freq="M")
    end = pd.Period(end_month, freq="M")
    return [str(p) for p in pd.period_range(start, end, freq="M")]


def _epoch_unit(series: pd.Series) -> str:
    values = pd.to_numeric(series, errors="raise")
    return "us" if values.abs().median() > 1e14 else "ms"


def _read_kline_zip(content: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        csvs = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csvs:
            raise RuntimeError("No CSV found in Binance archive")
        with zf.open(csvs[0]) as fh:
            raw = pd.read_csv(fh)
        if "open_time" in raw.columns:
            raw = raw.rename(columns={
                "open_time": "timestamp",
                "quote_asset_volume": "quote_volume",
                "number_of_trades": "trades",
                "taker_buy_base_asset_volume": "taker_buy_base",
                "taker_buy_quote_asset_volume": "taker_buy_quote",
            })
        elif "timestamp" not in raw.columns:
            with zf.open(csvs[0]) as fh:
                raw = pd.read_csv(fh, header=None, names=KLINE_COLUMNS)
    unit = _epoch_unit(raw["timestamp"])
    raw["timestamp"] = pd.to_datetime(pd.to_numeric(raw["timestamp"], errors="raise"), unit=unit, utc=True).astype("datetime64[ms, UTC]")
    close_unit = _epoch_unit(raw["close_time"])
    raw["close_time"] = pd.to_datetime(pd.to_numeric(raw["close_time"], errors="raise"), unit=close_unit, utc=True).astype("datetime64[ms, UTC]")
    for col in ["open", "high", "low", "close", "volume"]:
        raw[col] = pd.to_numeric(raw[col], errors="raise")
    return raw[KLINE_COLUMNS].sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def _download_month(url: str, timeout: int = 60) -> pd.DataFrame | None:
    response = requests.get(url, timeout=timeout)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return _read_kline_zip(response.content)


def _download_pair(symbol: str, month: str) -> tuple[str, pd.DataFrame | None, pd.DataFrame | None]:
    spot_url = f"{SPOT_MONTHLY}/{symbol}/1m/{symbol}-1m-{month}.zip"
    perp_url = f"{FUTURES_MONTHLY}/{symbol}/1m/{symbol}-1m-{month}.zip"
    with ThreadPoolExecutor(max_workers=2) as pool:
        s = pool.submit(_download_month, spot_url)
        p = pool.submit(_download_month, perp_url)
        return month, s.result(), p.result()


def backfill_monthly(config: BasisResearchConfig, store: AppendOnlyMarketStore, workers: int = 6) -> dict:
    months = _month_range(config.start_month, config.end_month)
    stats: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_download_pair, config.symbol, month): month for month in months}
        for future in as_completed(futures):
            month, spot, perp = future.result()
            row = {"month": month, "spot_rows": 0, "perp_rows": 0, "spot_written": 0, "perp_written": 0}
            if spot is not None:
                spot = spot.assign(symbol=config.symbol, source="binance_vision_spot")
                row["spot_rows"] = len(spot)
                row["spot_written"] = store.append("spot_ohlcv", spot[["timestamp", "close_time", "symbol", "source", "open", "high", "low", "close", "volume"]])["written"]
            if perp is not None:
                perp = perp.assign(symbol=config.symbol, source="binance_vision_usdm")
                row["perp_rows"] = len(perp)
                row["perp_written"] = store.append("futures_ohlcv", perp[["timestamp", "close_time", "symbol", "source", "open", "high", "low", "close", "volume"]])["written"]
            stats.append(row)
    stats.sort(key=lambda x: x["month"])
    return {
        "months_requested": len(months),
        "months_with_spot": sum(x["spot_rows"] > 0 for x in stats),
        "months_with_perp": sum(x["perp_rows"] > 0 for x in stats),
        "spot_rows_downloaded": sum(x["spot_rows"] for x in stats),
        "perp_rows_downloaded": sum(x["perp_rows"] for x in stats),
        "spot_rows_written": sum(x["spot_written"] for x in stats),
        "perp_rows_written": sum(x["perp_written"] for x in stats),
        "monthly": stats,
    }


def build_aligned_basis(store: AppendOnlyMarketStore, symbol: str) -> tuple[pd.DataFrame, dict]:
    cols = ["timestamp", "close", "symbol", "source"]
    spot = store.read("spot_ohlcv", symbol=symbol, source="binance_vision_spot", columns=cols)
    perp = store.read("futures_ohlcv", symbol=symbol, source="binance_vision_usdm", columns=cols)
    if spot.empty or perp.empty:
        raise RuntimeError("Spot or perpetual backfill is empty")
    spot = spot.rename(columns={"close": "spot_close"})[["timestamp", "spot_close"]]
    perp = perp.rename(columns={"close": "perp_close"})[["timestamp", "perp_close"]]
    spot = spot.sort_values("timestamp").drop_duplicates("timestamp")
    perp = perp.sort_values("timestamp").drop_duplicates("timestamp")
    aligned = spot.merge(perp, on="timestamp", how="inner", validate="one_to_one")
    aligned["basis_abs"] = aligned["perp_close"] - aligned["spot_close"]
    aligned["basis_pct"] = aligned["basis_abs"] / aligned["spot_close"]
    aligned["basis_bps"] = aligned["basis_pct"] * 10_000.0
    expected_start = max(spot["timestamp"].min(), perp["timestamp"].min())
    expected_end = min(spot["timestamp"].max(), perp["timestamp"].max())
    expected_minutes = int((expected_end - expected_start) / pd.Timedelta(minutes=1)) + 1
    diagnostics = {
        "spot_rows": len(spot),
        "perp_rows": len(perp),
        "aligned_rows": len(aligned),
        "aligned_start": aligned["timestamp"].min().isoformat(),
        "aligned_end": aligned["timestamp"].max().isoformat(),
        "expected_common_minutes": expected_minutes,
        "missing_common_minutes": expected_minutes - len(aligned),
        "spot_only_timestamps": len(spot.merge(perp[["timestamp"]], on="timestamp", how="left", indicator=True).query("_merge == 'left_only'")),
        "perp_only_timestamps": len(perp.merge(spot[["timestamp"]], on="timestamp", how="left", indicator=True).query("_merge == 'left_only'")),
        "duplicates_after_alignment": int(aligned["timestamp"].duplicated().sum()),
        "monotonic_utc": bool(aligned["timestamp"].is_monotonic_increasing),
    }
    return aligned.sort_values("timestamp").reset_index(drop=True), diagnostics


def add_research_features(df: pd.DataFrame, config: BasisResearchConfig) -> pd.DataFrame:
    out = df.copy()
    basis_mean = out["basis_pct"].rolling(config.z_window_minutes, min_periods=config.z_window_minutes).mean().shift(1)
    basis_std = out["basis_pct"].rolling(config.z_window_minutes, min_periods=config.z_window_minutes).std(ddof=0).shift(1)
    out["basis_mean_30d"] = basis_mean
    out["basis_std_30d"] = basis_std
    out["basis_zscore"] = (out["basis_pct"] - basis_mean) / basis_std.replace(0, np.nan)

    log_ret = np.log(out["spot_close"] / out["spot_close"].shift(1))
    out["spot_vol_24h"] = log_ret.rolling(config.volatility_window_minutes, min_periods=config.volatility_window_minutes).std(ddof=0) * math.sqrt(config.volatility_window_minutes)
    vol_ref = out["spot_vol_24h"].rolling(config.volatility_reference_minutes, min_periods=7 * 24 * 60).median().shift(1)
    out["vol_regime"] = np.where(out["spot_vol_24h"] > vol_ref, "HIGH_VOL", "LOW_VOL")
    out.loc[vol_ref.isna(), "vol_regime"] = "UNCLASSIFIED"

    trend_ret = out["spot_close"] / out["spot_close"].shift(config.trend_lookback_minutes) - 1.0
    out["trend_7d_return"] = trend_ret
    out["trend_regime"] = np.select(
        [trend_ret >= config.trend_threshold, trend_ret <= -config.trend_threshold],
        ["BULL", "BEAR"],
        default="SIDEWAYS",
    )
    out.loc[trend_ret.isna(), "trend_regime"] = "UNCLASSIFIED"
    for label, minutes in MINUTES.items():
        out[f"future_basis_change_{label}"] = out["basis_pct"].shift(-minutes) - out["basis_pct"]
        out[f"future_basis_z_{label}"] = out["basis_zscore"].shift(-minutes)
        out[f"future_perp_return_{label}"] = out["perp_close"].shift(-minutes) / out["perp_close"] - 1.0
    return out


def identify_extreme_episodes(df: pd.DataFrame, config: BasisResearchConfig) -> pd.DataFrame:
    events: list[int] = []
    active: str | None = None
    for idx, z in df["basis_zscore"].items():
        if pd.isna(z):
            continue
        if active is None:
            if z >= config.extreme_z:
                events.append(idx)
                active = "POSITIVE"
            elif z <= -config.extreme_z:
                events.append(idx)
                active = "NEGATIVE"
        elif abs(z) <= config.reset_z:
            active = None
    event_df = df.loc[events].copy()
    event_df["extreme_side"] = np.where(event_df["basis_zscore"] > 0, "POSITIVE", "NEGATIVE")
    return event_df


def _bootstrap_ci(values: pd.Series, seed: int = 42, draws: int = 1000) -> tuple[float | None, float | None]:
    clean = values.dropna().to_numpy(dtype=float)
    if len(clean) < 5:
        return None, None
    rng = np.random.default_rng(seed)
    means = np.empty(draws)
    for i in range(draws):
        means[i] = rng.choice(clean, size=len(clean), replace=True).mean()
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def summarize_event_group(group: pd.DataFrame, horizon: str) -> dict:
    delta = group[f"future_basis_change_{horizon}"]
    direction = np.where(group["extreme_side"] == "POSITIVE", -1.0, 1.0)
    toward_mean = pd.Series(delta.to_numpy() * direction, index=group.index)
    future_z = group[f"future_basis_z_{horizon}"]
    perp_ret = group[f"future_perp_return_{horizon}"]
    lo, hi = _bootstrap_ci(toward_mean)
    return {
        "horizon": horizon,
        "events": int(len(group)),
        "mean_reversion_hit_rate": float((toward_mean > 0).mean()) if len(group) else None,
        "mean_toward_mean_change_bps": float(toward_mean.mean() * 10_000) if len(group) else None,
        "median_toward_mean_change_bps": float(toward_mean.median() * 10_000) if len(group) else None,
        "bootstrap_mean_ci_low_bps": None if lo is None else lo * 10_000,
        "bootstrap_mean_ci_high_bps": None if hi is None else hi * 10_000,
        "reverted_inside_1z_rate": float((future_z.abs() <= 1.0).mean()) if future_z.notna().any() else None,
        "mean_perp_return": float(perp_ret.mean()) if perp_ret.notna().any() else None,
        "median_perp_return": float(perp_ret.median()) if perp_ret.notna().any() else None,
        "perp_up_rate": float((perp_ret > 0).mean()) if perp_ret.notna().any() else None,
    }


def run_analysis(df: pd.DataFrame, config: BasisResearchConfig) -> dict[str, pd.DataFrame | dict]:
    featured = add_research_features(df, config)
    events = identify_extreme_episodes(featured, config)
    quantiles = featured["basis_bps"].quantile([0.001, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 0.999])
    distribution = {
        "count": int(featured["basis_bps"].notna().sum()),
        "mean_bps": float(featured["basis_bps"].mean()),
        "std_bps": float(featured["basis_bps"].std(ddof=0)),
        "min_bps": float(featured["basis_bps"].min()),
        "max_bps": float(featured["basis_bps"].max()),
        "quantiles_bps": {str(k): float(v) for k, v in quantiles.items()},
        "zscore_window_minutes": config.z_window_minutes,
        "extreme_z": config.extreme_z,
        "positive_extreme_events": int((events["extreme_side"] == "POSITIVE").sum()) if not events.empty else 0,
        "negative_extreme_events": int((events["extreme_side"] == "NEGATIVE").sum()) if not events.empty else 0,
    }

    rows: list[dict] = []
    dimensions = [
        ("ALL", [("ALL", events)]),
        ("EXTREME_SIDE", list(events.groupby("extreme_side")) if not events.empty else []),
        ("VOL_REGIME", list(events[events["vol_regime"] != "UNCLASSIFIED"].groupby("vol_regime")) if not events.empty else []),
        ("TREND_REGIME", list(events[events["trend_regime"] != "UNCLASSIFIED"].groupby("trend_regime")) if not events.empty else []),
    ]
    for dimension, groups in dimensions:
        for group_name, group in groups:
            for horizon in MINUTES:
                summary = summarize_event_group(group, horizon)
                rows.append({"dimension": dimension, "group": group_name, **summary})
    event_study = pd.DataFrame(rows)

    price_direction = []
    if not events.empty:
        for side, group in events.groupby("extreme_side"):
            for horizon in MINUTES:
                ret = group[f"future_perp_return_{horizon}"].dropna()
                price_direction.append({
                    "extreme_side": side,
                    "horizon": horizon,
                    "events": len(ret),
                    "mean_perp_return": ret.mean() if len(ret) else np.nan,
                    "median_perp_return": ret.median() if len(ret) else np.nan,
                    "up_rate": (ret > 0).mean() if len(ret) else np.nan,
                })
    return {
        "featured": featured,
        "events": events,
        "distribution": distribution,
        "event_study": event_study,
        "price_direction": pd.DataFrame(price_direction),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill official Binance 1m spot/perpetual data and run non-strategy basis research")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start-month", default="2019-09")
    parser.add_argument("--end-month", default="2026-06")
    parser.add_argument("--store-root", default="data/arena/market_store")
    parser.add_argument("--output-dir", default="data/arena/basis_research")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    config = BasisResearchConfig(symbol=args.symbol, start_month=args.start_month, end_month=args.end_month)
    store = AppendOnlyMarketStore(args.store_root)
    backfill = backfill_monthly(config, store, workers=args.workers)
    aligned, alignment = build_aligned_basis(store, args.symbol)
    analysis = run_analysis(aligned, config)

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    aligned.to_parquet(out / "btc_spot_perp_basis_1m.parquet", index=False)
    analysis["events"].to_csv(out / "basis_extreme_events.csv", index=False)
    analysis["event_study"].to_csv(out / "basis_event_study.csv", index=False)
    analysis["price_direction"].to_csv(out / "basis_price_direction.csv", index=False)

    summary = {
        "config": asdict(config),
        "backfill": backfill,
        "alignment": alignment,
        "distribution": analysis["distribution"],
        "methodology": {
            "basis": "(perpetual_1m_close - spot_1m_close) / spot_1m_close",
            "zscore": "current basis versus prior 30-day rolling mean/std; rolling statistics shifted by one minute",
            "extremes": "episode starts at +/-2 z; no new event until abs(z) returns to <=1",
            "horizons": list(MINUTES),
            "volatility_regime": "24h realized spot volatility versus prior rolling 30-day median",
            "trend_regime": "7-day backward spot return: bull >= +5%, bear <= -5%, otherwise sideways",
            "strategy_rules_created": False,
            "parameter_optimization": False,
            "live_trading": False,
        },
    }
    (out / "basis_research_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "period": [alignment["aligned_start"], alignment["aligned_end"]],
        "aligned_rows": alignment["aligned_rows"],
        "missing_common_minutes": alignment["missing_common_minutes"],
        "positive_extreme_events": analysis["distribution"]["positive_extreme_events"],
        "negative_extreme_events": analysis["distribution"]["negative_extreme_events"],
        "output_dir": str(out),
        "live_trading": False,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
