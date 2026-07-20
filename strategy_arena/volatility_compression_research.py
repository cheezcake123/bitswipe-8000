from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from strategy_arena.basis_execution import fetch_funding
from strategy_arena.trend_breakout_backtest import load_hourly_futures


@dataclass(frozen=True)
class CompressionResearchConfig:
    symbol: str = "BTCUSDT"
    start_month: str = "2019-09"
    end_month: str = "2026-06"
    evaluation_start: str = "2020-01-01T00:00:00Z"
    percentile_lookback_hours: int = 90 * 24
    compression_percentile: float = 0.20
    bb_window_hours: int = 20
    bb_std: float = 2.0
    atr_window_hours: int = 24
    range_window_hours: int = 24
    volume_window_hours: int = 24
    regime_lookback_hours: int = 7 * 24
    bull_threshold: float = 0.05
    bear_threshold: float = -0.05
    high_funding_rate: float = 0.0002  # Frozen Funding Carry v1 definition: 2bp/event.


HORIZONS = (1, 4, 12, 24, 72)


def _atr_pct(data: pd.DataFrame, window: int) -> pd.Series:
    prev_close = data["close"].shift(1)
    tr = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - prev_close).abs(),
            (data["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window, min_periods=window).mean() / data["close"]


def add_features(data: pd.DataFrame, funding: pd.DataFrame, cfg: CompressionResearchConfig) -> pd.DataFrame:
    out = data.copy().sort_values("timestamp").reset_index(drop=True)
    out["log_return"] = np.log(out["close"] / out["close"].shift(1))
    out["realized_vol_24h"] = out["log_return"].rolling(24, min_periods=24).std(ddof=0) * np.sqrt(24)
    out["future_realized_vol_24h"] = out["log_return"].rolling(24, min_periods=24).std(ddof=0).shift(-24) * np.sqrt(24)

    mid = out["close"].rolling(cfg.bb_window_hours, min_periods=cfg.bb_window_hours).mean()
    sd = out["close"].rolling(cfg.bb_window_hours, min_periods=cfg.bb_window_hours).std(ddof=0)
    out["bb_width"] = (2 * cfg.bb_std * sd) / mid
    out["atr_pct"] = _atr_pct(out, cfg.atr_window_hours)

    # Compression must be known before the breakout candle. Both the observed metric and
    # its percentile reference are shifted so the breakout candle cannot define its own compression state.
    for name, metric in (("BB", out["bb_width"]), ("ATR", out["atr_pct"])):
        known_metric = metric.shift(1)
        threshold = known_metric.rolling(
            cfg.percentile_lookback_hours,
            min_periods=30 * 24,
        ).quantile(cfg.compression_percentile)
        out[f"{name.lower()}_compression_threshold"] = threshold
        out[f"{name.lower()}_compressed"] = known_metric <= threshold

    out["range_upper"] = out["high"].rolling(cfg.range_window_hours, min_periods=cfg.range_window_hours).max().shift(1)
    out["range_lower"] = out["low"].rolling(cfg.range_window_hours, min_periods=cfg.range_window_hours).min().shift(1)
    out["volume_reference"] = out["volume"].shift(1).rolling(cfg.volume_window_hours, min_periods=cfg.volume_window_hours).median()
    out["volume_surge"] = out["volume"] > out["volume_reference"]

    trend_ret = out["close"] / out["close"].shift(cfg.regime_lookback_hours) - 1.0
    out["trend_regime"] = np.select(
        [trend_ret >= cfg.bull_threshold, trend_ret <= cfg.bear_threshold],
        ["BULL", "BEAR"],
        default="SIDEWAYS",
    )
    out.loc[trend_ret.isna(), "trend_regime"] = "UNCLASSIFIED"

    # Funding regime uses only the last realized funding event known at or before the candle.
    if funding.empty:
        out["last_realized_funding_rate"] = np.nan
    else:
        f = funding[["timestamp", "funding_rate"]].copy().sort_values("timestamp")
        f["timestamp"] = pd.to_datetime(f["timestamp"], utc=True).astype("datetime64[ms, UTC]")
        left = out[["timestamp"]].copy()
        left["timestamp"] = pd.to_datetime(left["timestamp"], utc=True).astype("datetime64[ms, UTC]")
        rates = pd.merge_asof(left, f, on="timestamp", direction="backward", allow_exact_matches=True)["funding_rate"]
        out["last_realized_funding_rate"] = rates.to_numpy()
    out["funding_regime"] = np.where(out["last_realized_funding_rate"] >= cfg.high_funding_rate, "HIGH_FUNDING", "NORMAL_FUNDING")

    for h in HORIZONS:
        raw_ret = out["close"].shift(-h) / out["close"] - 1.0
        out[f"future_return_{h}h"] = raw_ret
    return out


def identify_events(featured: pd.DataFrame, method: str) -> pd.DataFrame:
    compressed = featured[f"{method.lower()}_compressed"].fillna(False)
    long_mask = compressed & (featured["close"] > featured["range_upper"])
    short_mask = compressed & (featured["close"] < featured["range_lower"])
    long = featured.loc[long_mask].copy()
    long["direction"] = "LONG"
    short = featured.loc[short_mask].copy()
    short["direction"] = "SHORT"
    events = pd.concat([long, short], ignore_index=False).sort_index().copy()
    events["compression_method"] = method
    for h in HORIZONS:
        sign = np.where(events["direction"] == "LONG", 1.0, -1.0)
        events[f"directional_return_{h}h"] = events[f"future_return_{h}h"] * sign
    events["volatility_expanded_24h"] = events["future_realized_vol_24h"] > events["realized_vol_24h"]
    events["false_breakout_24h"] = events["directional_return_24h"] <= 0
    return events


def add_path_stats(events: pd.DataFrame, full: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return events
    out = events.copy()
    full = full.reset_index(drop=True)
    index_by_timestamp = pd.Series(full.index.to_numpy(), index=full["timestamp"])
    for horizon in (24, 72):
        mfe, mae = [], []
        for _, row in out.iterrows():
            i = int(index_by_timestamp.loc[row["timestamp"]])
            window = full.iloc[i + 1 : i + horizon + 1]
            entry = float(row["close"])
            if window.empty:
                mfe.append(np.nan)
                mae.append(np.nan)
                continue
            if row["direction"] == "LONG":
                mfe.append(float(window["high"].max() / entry - 1.0))
                mae.append(float(window["low"].min() / entry - 1.0))
            else:
                mfe.append(float(1.0 - window["low"].min() / entry))
                mae.append(float(1.0 - window["high"].max() / entry))
        out[f"mfe_{horizon}h"] = mfe
        out[f"mae_{horizon}h"] = mae
    return out


def _summary_rows(events: pd.DataFrame, dimension: str, group_col: str | None = None) -> list[dict]:
    if events.empty:
        return []
    groups = [("ALL", events)] if group_col is None else list(events.groupby(group_col, dropna=False))
    rows: list[dict] = []
    for group, g in groups:
        for direction, d in g.groupby("direction"):
            for h in HORIZONS:
                values = d[f"directional_return_{h}h"].dropna()
                rows.append({
                    "dimension": dimension,
                    "group": str(group),
                    "direction": direction,
                    "horizon_hours": h,
                    "events": int(len(values)),
                    "mean_directional_return": float(values.mean()) if len(values) else np.nan,
                    "median_directional_return": float(values.median()) if len(values) else np.nan,
                    "positive_rate": float((values > 0).mean()) if len(values) else np.nan,
                    "volatility_expansion_rate_24h": float(d["volatility_expanded_24h"].mean()),
                    "false_breakout_rate_24h": float(d["false_breakout_24h"].mean()),
                    "mean_mfe_24h": float(d["mfe_24h"].mean()),
                    "mean_mae_24h": float(d["mae_24h"].mean()),
                    "mean_mfe_72h": float(d["mfe_72h"].mean()),
                    "mean_mae_72h": float(d["mae_72h"].mean()),
                })
    return rows


def run_research(data: pd.DataFrame, funding: pd.DataFrame, cfg: CompressionResearchConfig) -> dict:
    featured = add_features(data, funding, cfg)
    featured = featured[featured["timestamp"] >= pd.Timestamp(cfg.evaluation_start)].copy().reset_index(drop=True)
    all_events = []
    for method in ("BB", "ATR"):
        events = identify_events(featured, method)
        events = add_path_stats(events, featured)
        all_events.append(events)
    events = pd.concat(all_events, ignore_index=True).sort_values("timestamp")
    events["year"] = pd.to_datetime(events["timestamp"], utc=True).dt.year
    events["recent_period"] = np.where(events["year"] >= 2024, "2024_2026", "2020_2023")
    events["volume_state"] = np.where(events["volume_surge"], "VOLUME_SURGE", "NORMAL_VOLUME")

    rows: list[dict] = []
    for method, m in events.groupby("compression_method"):
        rows.extend(_summary_rows(m, f"{method}_ALL"))
        rows.extend(_summary_rows(m, f"{method}_VOLUME", "volume_state"))
        rows.extend(_summary_rows(m[m["trend_regime"] != "UNCLASSIFIED"], f"{method}_TREND", "trend_regime"))
        rows.extend(_summary_rows(m, f"{method}_FUNDING", "funding_regime"))
        rows.extend(_summary_rows(m, f"{method}_YEAR", "year"))
        rows.extend(_summary_rows(m, f"{method}_RECENT", "recent_period"))
    summary_table = pd.DataFrame(rows)

    method_comparison = []
    for method, m in events.groupby("compression_method"):
        for direction, d in m.groupby("direction"):
            method_comparison.append({
                "compression_method": method,
                "direction": direction,
                "events": int(len(d)),
                "volatility_expansion_rate_24h": float(d["volatility_expanded_24h"].mean()),
                "mean_directional_return_24h": float(d["directional_return_24h"].mean()),
                "positive_rate_24h": float((d["directional_return_24h"] > 0).mean()),
                "false_breakout_rate_24h": float(d["false_breakout_24h"].mean()),
                "mean_mfe_24h": float(d["mfe_24h"].mean()),
                "mean_mae_24h": float(d["mae_24h"].mean()),
                "recent_2024_2026_events": int((d["year"] >= 2024).sum()),
                "recent_mean_directional_return_24h": float(d.loc[d["year"] >= 2024, "directional_return_24h"].mean()),
                "recent_positive_rate_24h": float((d.loc[d["year"] >= 2024, "directional_return_24h"] > 0).mean()),
            })
    return {"featured": featured, "events": events, "summary_table": summary_table, "method_comparison": pd.DataFrame(method_comparison)}


def main() -> None:
    p = argparse.ArgumentParser(description="Research volatility compression breakout relationships; no trading orders")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--start-month", default="2019-09")
    p.add_argument("--end-month", default="2026-06")
    p.add_argument("--evaluation-start", default="2020-01-01T00:00:00Z")
    p.add_argument("--output-dir", default="data/arena/volatility_compression_research")
    p.add_argument("--workers", type=int, default=6)
    args = p.parse_args()

    cfg = CompressionResearchConfig(
        symbol=args.symbol,
        start_month=args.start_month,
        end_month=args.end_month,
        evaluation_start=args.evaluation_start,
    )
    data = load_hourly_futures(args.symbol, args.start_month, args.end_month, args.workers)
    funding = fetch_funding(args.symbol, args.start_month, args.end_month)
    result = run_research(data, funding, cfg)

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    result["events"].to_csv(out / "compression_breakout_events.csv", index=False)
    result["summary_table"].to_csv(out / "relationship_summary.csv", index=False)
    result["method_comparison"].to_csv(out / "method_comparison.csv", index=False)

    expected = int((data["timestamp"].max() - data["timestamp"].min()) / pd.Timedelta(hours=1)) + 1
    summary = {
        "config": asdict(cfg),
        "data": {
            "start": data["timestamp"].min().isoformat(),
            "end": data["timestamp"].max().isoformat(),
            "hourly_rows": int(len(data)),
            "expected_hours": expected,
            "missing_hours": int(expected - len(data)),
            "funding_events": int(len(funding)),
            "source": "Binance Vision USD-M official public archives",
        },
        "method_comparison": result["method_comparison"].replace({np.nan: None}).to_dict(orient="records"),
        "methodology": {
            "compression": "Compare only BB Width and ATR%, each <= prior rolling 90-day 20th percentile; no percentile sweep.",
            "breakout": "Close outside prior 24-hour high/low while prior candle was compressed.",
            "execution": "Relationship study only; no strategy fills yet.",
            "lookahead": "Compression metrics, percentile thresholds, range and volume references are shifted to past-only information.",
            "false_breakout": "Directional 24-hour close-to-close return <= 0 after breakout.",
            "funding_regime": "HIGH_FUNDING only when last already-realized funding event >= 2bp; otherwise NORMAL_FUNDING.",
            "oi_regime": "Not included in long-horizon study because reliable historical OI coverage is not available for 2020-2026 from the current free official dataset.",
            "parameter_optimization": False,
            "live_trading": False,
        },
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
