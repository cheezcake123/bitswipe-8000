from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from strategy_arena.basis_execution import fetch_funding
from strategy_arena.basis_research import (
    BasisResearchConfig,
    SPOT_MONTHLY,
    FUTURES_MONTHLY,
    _month_range,
    add_research_features,
    build_aligned_basis,
)
from strategy_arena.market_store import AppendOnlyMarketStore
from strategy_arena.trend_breakout_research import _read_kline_zip


@dataclass(frozen=True)
class FundingCarryResearchConfig:
    symbol: str = "BTCUSDT"
    start_month: str = "2019-09"
    end_month: str = "2026-06"
    analysis_start: str = "2020-01-01T00:00:00Z"
    high_funding_rate: float = 0.0001  # 1 bp/event descriptive bucket only; not a chosen trading threshold yet.


def _safe(value):
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        value = float(value)
        return None if not np.isfinite(value) else value
    return value


def _download_archive(url: str) -> pd.DataFrame | None:
    r = requests.get(url, timeout=60)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return _read_kline_zip(r.content)


def _download_pair(symbol: str, month: str) -> tuple[str, pd.DataFrame | None, pd.DataFrame | None]:
    spot_url = f"{SPOT_MONTHLY}/{symbol}/1m/{symbol}-1m-{month}.zip"
    perp_url = f"{FUTURES_MONTHLY}/{symbol}/1m/{symbol}-1m-{month}.zip"
    with ThreadPoolExecutor(max_workers=2) as pool:
        spot = pool.submit(_download_archive, spot_url)
        perp = pool.submit(_download_archive, perp_url)
        return month, spot.result(), perp.result()


def backfill_monthly_robust(config: BasisResearchConfig, store: AppendOnlyMarketStore, workers: int = 6) -> dict:
    months = _month_range(config.start_month, config.end_month)
    stats = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_download_pair, config.symbol, month) for month in months]
        for future in as_completed(futures):
            month, spot, perp = future.result()
            row = {"month": month, "spot_rows": 0, "perp_rows": 0, "spot_written": 0, "perp_written": 0}
            if spot is not None:
                spot = spot.assign(symbol=config.symbol, source="binance_vision_spot")
                payload = spot[["timestamp", "close_time", "symbol", "source", "open", "high", "low", "close", "volume"]]
                row["spot_rows"] = len(payload)
                row["spot_written"] = store.append("spot_ohlcv", payload)["written"]
            if perp is not None:
                perp = perp.assign(symbol=config.symbol, source="binance_vision_usdm")
                payload = perp[["timestamp", "close_time", "symbol", "source", "open", "high", "low", "close", "volume"]]
                row["perp_rows"] = len(payload)
                row["perp_written"] = store.append("futures_ohlcv", payload)["written"]
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


def _funding_bucket(rate: pd.Series) -> pd.Series:
    return pd.cut(
        rate,
        bins=[-np.inf, 0.0, 0.00005, 0.0001, 0.0002, np.inf],
        labels=["NEGATIVE", "POS_0_0.5BP", "POS_0.5_1BP", "POS_1_2BP", "POS_GE_2BP"],
        right=False,
    ).astype(str)


def _attach_market_context(funding: pd.DataFrame, featured: pd.DataFrame) -> pd.DataFrame:
    market = featured[[
        "timestamp", "basis_bps", "basis_pct", "spot_close", "perp_close",
        "vol_regime", "trend_regime",
    ]].sort_values("timestamp")
    return pd.merge_asof(
        funding.sort_values("timestamp"),
        market,
        on="timestamp",
        direction="backward",
        allow_exact_matches=True,
        tolerance=pd.Timedelta(minutes=1),
    )


def _add_forward_relationships(events: pd.DataFrame) -> pd.DataFrame:
    out = events.copy().reset_index(drop=True)
    ts_ns = out["timestamp"].astype("datetime64[ns, UTC]").astype("int64").to_numpy()
    rates = out["funding_rate"].to_numpy(dtype=float)
    prefix = np.concatenate([[0.0], np.cumsum(rates)])
    for hours in (8, 24, 72):
        horizon_ns = int(pd.Timedelta(hours=hours).value)
        ends = np.searchsorted(ts_ns, ts_ns + horizon_ns, side="right")
        out[f"future_cumulative_funding_{hours}h"] = [prefix[e] - prefix[i + 1] for i, e in enumerate(ends)]
    return out


def _episode_table(events: pd.DataFrame) -> pd.DataFrame:
    e = events.copy().sort_values("timestamp").reset_index(drop=True)
    sign = np.select([e["funding_rate"] > 0, e["funding_rate"] < 0], [1, -1], default=0)
    e["sign"] = sign
    e["episode_id"] = pd.Series(sign).ne(pd.Series(sign).shift()).cumsum()
    rows = []
    for episode_id, g in e.groupby("episode_id"):
        s = int(g["sign"].iloc[0])
        if s == 0:
            continue
        typical_gap_h = float(g["timestamp"].diff().dropna().dt.total_seconds().median() / 3600) if len(g) > 1 else 8.0
        duration_h = float((g["timestamp"].iloc[-1] - g["timestamp"].iloc[0]).total_seconds() / 3600 + typical_gap_h)
        rows.append({
            "episode_id": int(episode_id),
            "side": "POSITIVE" if s > 0 else "NEGATIVE",
            "start": g["timestamp"].iloc[0],
            "end": g["timestamp"].iloc[-1],
            "events": int(len(g)),
            "duration_hours": duration_h,
            "mean_funding_rate": float(g["funding_rate"].mean()),
            "max_funding_rate": float(g["funding_rate"].max()),
            "min_funding_rate": float(g["funding_rate"].min()),
            "cumulative_funding_rate": float(g["funding_rate"].sum()),
            "basis_start_bps": float(g["basis_bps"].iloc[0]) if pd.notna(g["basis_bps"].iloc[0]) else np.nan,
            "basis_end_bps": float(g["basis_bps"].iloc[-1]) if pd.notna(g["basis_bps"].iloc[-1]) else np.nan,
        })
    return pd.DataFrame(rows)


def _basis_at_times(featured: pd.DataFrame, times: pd.Series) -> pd.Series:
    left = pd.DataFrame({"timestamp": pd.to_datetime(times, utc=True)}).sort_values("timestamp")
    right = featured[["timestamp", "basis_bps"]].sort_values("timestamp")
    return pd.merge_asof(left, right, on="timestamp", direction="backward", tolerance=pd.Timedelta(minutes=1))["basis_bps"]


def _episode_end_context(episodes: pd.DataFrame, featured: pd.DataFrame) -> pd.DataFrame:
    pos = episodes[episodes["side"] == "POSITIVE"].copy()
    if pos.empty:
        return pos
    for label, delta in (("pre_8h", -pd.Timedelta(hours=8)), ("post_8h", pd.Timedelta(hours=8)), ("post_24h", pd.Timedelta(hours=24))):
        vals = _basis_at_times(featured, pos["end"] + delta)
        vals.index = pos.index
        pos[f"basis_{label}_bps"] = vals
    pos["basis_change_end_to_post_8h_bps"] = pos["basis_post_8h_bps"] - pos["basis_end_bps"]
    pos["basis_change_end_to_post_24h_bps"] = pos["basis_post_24h_bps"] - pos["basis_end_bps"]
    return pos


def _group_summary(events: pd.DataFrame, group_col: str) -> pd.DataFrame:
    rows = []
    for name, g in events.groupby(group_col, dropna=False):
        rows.append({
            "group": str(name),
            "events": int(len(g)),
            "positive_rate": float((g["funding_rate"] > 0).mean()),
            "mean_funding_rate": float(g["funding_rate"].mean()),
            "median_funding_rate": float(g["funding_rate"].median()),
            "mean_basis_bps": float(g["basis_bps"].mean()),
            "mean_future_funding_24h": float(g["future_cumulative_funding_24h"].mean()),
            "mean_future_funding_72h": float(g["future_cumulative_funding_72h"].mean()),
        })
    return pd.DataFrame(rows)


def run_research(aligned: pd.DataFrame, funding: pd.DataFrame, config: FundingCarryResearchConfig) -> dict:
    basis_cfg = BasisResearchConfig(symbol=config.symbol, start_month=config.start_month, end_month=config.end_month)
    featured = add_research_features(aligned, basis_cfg)
    start = pd.Timestamp(config.analysis_start)
    featured = featured[featured["timestamp"] >= start].copy()
    funding = funding[funding["timestamp"] >= start].copy().sort_values("timestamp").drop_duplicates("timestamp")
    events = _attach_market_context(funding, featured)
    events = _add_forward_relationships(events)
    events["funding_bucket"] = _funding_bucket(events["funding_rate"])
    events["funding_regime"] = np.where(events["funding_rate"] >= config.high_funding_rate, "HIGH_FUNDING", "NORMAL_FUNDING")
    events["year"] = events["timestamp"].dt.year

    for hours in (1, 8, 24):
        future = _basis_at_times(featured, events["timestamp"] + pd.Timedelta(hours=hours))
        future.index = events.index
        events[f"future_basis_change_{hours}h_bps"] = future - events["basis_bps"]

    episodes = _episode_table(events)
    episode_end = _episode_end_context(episodes, featured)

    q = events["funding_rate"].quantile([0, .01, .05, .25, .5, .75, .9, .95, .99, .999, 1])
    distribution = {
        "events": int(len(events)),
        "positive_events": int((events["funding_rate"] > 0).sum()),
        "negative_events": int((events["funding_rate"] < 0).sum()),
        "zero_events": int((events["funding_rate"] == 0).sum()),
        "positive_fraction": float((events["funding_rate"] > 0).mean()),
        "mean": float(events["funding_rate"].mean()),
        "median": float(events["funding_rate"].median()),
        "quantiles": {str(k): float(v) for k, v in q.items()},
        "high_funding_descriptive_threshold": config.high_funding_rate,
    }

    positive_episodes = episodes[episodes["side"] == "POSITIVE"]
    negative_episodes = episodes[episodes["side"] == "NEGATIVE"]
    episode_summary = {
        "positive_episode_count": int(len(positive_episodes)),
        "positive_median_events": float(positive_episodes["events"].median()) if len(positive_episodes) else None,
        "positive_median_hours": float(positive_episodes["duration_hours"].median()) if len(positive_episodes) else None,
        "positive_p90_hours": float(positive_episodes["duration_hours"].quantile(.9)) if len(positive_episodes) else None,
        "negative_episode_count": int(len(negative_episodes)),
        "negative_median_events": float(negative_episodes["events"].median()) if len(negative_episodes) else None,
        "negative_median_hours": float(negative_episodes["duration_hours"].median()) if len(negative_episodes) else None,
    }

    bucket_rows = []
    for bucket, g in events.groupby("funding_bucket"):
        bucket_rows.append({
            "funding_bucket": bucket,
            "events": int(len(g)),
            "mean_rate": float(g["funding_rate"].mean()),
            "median_rate": float(g["funding_rate"].median()),
            "mean_basis_bps": float(g["basis_bps"].mean()),
            "mean_future_funding_24h": float(g["future_cumulative_funding_24h"].mean()),
            "mean_future_funding_72h": float(g["future_cumulative_funding_72h"].mean()),
            "mean_basis_change_1h_bps": float(g["future_basis_change_1h_bps"].mean()),
            "mean_basis_change_8h_bps": float(g["future_basis_change_8h_bps"].mean()),
            "mean_basis_change_24h_bps": float(g["future_basis_change_24h_bps"].mean()),
        })

    return {
        "featured": featured,
        "events": events,
        "episodes": episodes,
        "episode_end": episode_end,
        "distribution": distribution,
        "episode_summary": episode_summary,
        "by_bucket": pd.DataFrame(bucket_rows),
        "by_year": _group_summary(events, "year"),
        "by_trend_regime": _group_summary(events[events["trend_regime"] != "UNCLASSIFIED"], "trend_regime"),
        "by_vol_regime": _group_summary(events[events["vol_regime"] != "UNCLASSIFIED"], "vol_regime"),
        "by_funding_regime": _group_summary(events, "funding_regime"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Funding Carry economic relationship study; research only, no orders")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start-month", default="2019-09")
    parser.add_argument("--end-month", default="2026-06")
    parser.add_argument("--store-root", default="data/arena/funding_carry_market_store")
    parser.add_argument("--output-dir", default="data/arena/funding_carry_research")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    config = FundingCarryResearchConfig(symbol=args.symbol, start_month=args.start_month, end_month=args.end_month)
    store = AppendOnlyMarketStore(args.store_root)
    backfill = backfill_monthly_robust(
        BasisResearchConfig(symbol=args.symbol, start_month=args.start_month, end_month=args.end_month),
        store,
        workers=args.workers,
    )
    aligned, alignment = build_aligned_basis(store, args.symbol)
    funding = fetch_funding(args.symbol, args.start_month, args.end_month)
    result = run_research(aligned, funding, config)

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    result["events"].to_csv(out / "funding_events_with_context.csv", index=False)
    result["episodes"].to_csv(out / "funding_sign_episodes.csv", index=False)
    result["episode_end"].to_csv(out / "positive_episode_end_context.csv", index=False)
    for name in ["by_bucket", "by_year", "by_trend_regime", "by_vol_regime", "by_funding_regime"]:
        result[name].to_csv(out / f"{name}.csv", index=False)

    summary = {
        "config": asdict(config),
        "backfill": backfill,
        "alignment": alignment,
        "funding_period": [funding["timestamp"].min().isoformat(), funding["timestamp"].max().isoformat()],
        "distribution": result["distribution"],
        "episode_summary": result["episode_summary"],
        "methodology": {
            "lookahead": "Realized funding is considered known only at fundingTime. Any eventual v1 entry must occur after that event; the trigger event payment cannot be collected retroactively.",
            "basis": "(perpetual 1m close - spot 1m close) / spot 1m close, aligned by exact minute timestamp",
            "funding_buckets": "Fixed absolute descriptive buckets; no performance threshold sweep",
            "regimes": "Same backward-looking 7d trend and 24h realized-volatility definitions used by prior Basis research",
            "parameter_optimization": False,
            "live_trading": False,
        },
    }
    (out / "funding_carry_research_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=_safe), encoding="utf-8")
    print(json.dumps({
        "period": alignment["aligned_start"],
        "aligned_end": alignment["aligned_end"],
        "funding_events": len(result["events"]),
        "positive_episodes": result["episode_summary"]["positive_episode_count"],
        "output_dir": str(out),
        "live_trading": False,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
