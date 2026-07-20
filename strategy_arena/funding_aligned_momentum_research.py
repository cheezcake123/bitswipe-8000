from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from strategy_arena.basis_execution import fetch_funding
from strategy_arena.trend_breakout_backtest import load_hourly_futures


HORIZONS = {"1h": 1, "4h": 4, "12h": 12, "24h": 24, "72h": 72, "7d": 168}


@dataclass(frozen=True)
class FundingAlignedMomentumResearchConfig:
    symbol: str = "BTCUSDT"
    start_month: str = "2019-09"
    end_month: str = "2026-06"
    evaluation_start: str = "2020-01-01T00:00:00Z"
    momentum_hours: tuple[int, ...] = (24, 168)


def _safe(value):
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        value = float(value)
        return None if not np.isfinite(value) else value
    return value


def attach_known_funding(hourly: pd.DataFrame, funding: pd.DataFrame) -> pd.DataFrame:
    out = hourly.copy().sort_values("timestamp").reset_index(drop=True)
    out["decision_timestamp"] = pd.to_datetime(out["decision_timestamp"], utc=True).astype("datetime64[ms, UTC]")
    f = funding[["timestamp", "funding_rate"]].copy().sort_values("timestamp")
    f["timestamp"] = pd.to_datetime(f["timestamp"], utc=True).astype("datetime64[ms, UTC]")
    merged = pd.merge_asof(
        out,
        f.rename(columns={"timestamp": "known_funding_timestamp", "funding_rate": "known_funding_rate"}),
        left_on="decision_timestamp",
        right_on="known_funding_timestamp",
        direction="backward",
        allow_exact_matches=True,
    )
    merged["known_funding_rate"] = merged["known_funding_rate"].fillna(0.0)
    return merged


def add_regimes(data: pd.DataFrame) -> pd.DataFrame:
    out = data.copy()
    trend_7d = out["close"] / out["close"].shift(168) - 1.0
    out["trend_regime"] = np.select(
        [trend_7d >= 0.05, trend_7d <= -0.05],
        ["BULL", "BEAR"],
        default="SIDEWAYS",
    )
    out.loc[trend_7d.isna(), "trend_regime"] = "UNCLASSIFIED"

    log_ret = np.log(out["close"] / out["close"].shift(1))
    rv24 = log_ret.rolling(24, min_periods=24).std(ddof=0) * math.sqrt(24)
    rv_ref = rv24.shift(1).rolling(30 * 24, min_periods=7 * 24).median()
    out["vol_regime"] = np.where(rv24 > rv_ref, "HIGH_VOL", "LOW_VOL")
    out.loc[rv_ref.isna(), "vol_regime"] = "UNCLASSIFIED"

    out["funding_regime"] = np.select(
        [out["known_funding_rate"] >= 0.0002, out["known_funding_rate"] > 0, out["known_funding_rate"] < 0],
        ["HIGH_POSITIVE_FUNDING", "MILD_POSITIVE_FUNDING", "NEGATIVE_FUNDING"],
        default="ZERO_FUNDING",
    )
    return out


def _future_extremes(data: pd.DataFrame, bars: int) -> tuple[pd.Series, pd.Series]:
    # Window begins after the signal bar, so the signal candle itself is excluded.
    future_high = data["high"].shift(-1).iloc[::-1].rolling(bars, min_periods=1).max().iloc[::-1]
    future_low = data["low"].shift(-1).iloc[::-1].rolling(bars, min_periods=1).min().iloc[::-1]
    return future_high, future_low


def _funding_prefix(funding: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    f = funding.copy().sort_values("timestamp")
    ts = pd.to_datetime(f["timestamp"], utc=True).astype("datetime64[ns, UTC]").astype("int64").to_numpy()
    rates = f["funding_rate"].to_numpy(dtype=float)
    prefix = np.concatenate([[0.0], np.cumsum(rates)])
    return ts, prefix


def _funding_sum_between(start_ns: np.ndarray, end_ns: np.ndarray, fts: np.ndarray, prefix: np.ndarray) -> np.ndarray:
    left = np.searchsorted(fts, start_ns, side="right")
    right = np.searchsorted(fts, end_ns, side="right")
    return prefix[right] - prefix[left]


def build_state_panel(data: pd.DataFrame, funding: pd.DataFrame, momentum_hours: int) -> pd.DataFrame:
    out = data.copy().reset_index(drop=True)
    out["momentum_return"] = out["close"] / out["close"].shift(momentum_hours) - 1.0
    out["momentum_direction"] = np.where(out["momentum_return"] > 0, 1, np.where(out["momentum_return"] < 0, -1, 0))
    out["funding_direction"] = np.where(out["known_funding_rate"] > 0, 1, np.where(out["known_funding_rate"] < 0, -1, 0))
    out["state"] = np.select(
        [
            (out["momentum_direction"] < 0) & (out["funding_direction"] > 0),
            (out["momentum_direction"] > 0) & (out["funding_direction"] < 0),
            (out["momentum_direction"] > 0) & (out["funding_direction"] > 0),
            (out["momentum_direction"] < 0) & (out["funding_direction"] < 0),
        ],
        ["A_NEG_MOM_POS_FUNDING", "B_POS_MOM_NEG_FUNDING", "C_POS_MOM_POS_FUNDING", "D_NEG_MOM_NEG_FUNDING"],
        default="OTHER",
    )
    out["aligned_position"] = out["momentum_direction"]

    close = out["close"]
    ts_ns = pd.to_datetime(out["decision_timestamp"], utc=True).astype("datetime64[ns, UTC]").astype("int64").to_numpy()
    fts, prefix = _funding_prefix(funding)

    for label, bars in HORIZONS.items():
        future_close = close.shift(-bars)
        raw_ret = future_close / close - 1.0
        aligned_ret = raw_ret * out["aligned_position"]
        future_high, future_low = _future_extremes(out, bars)
        long_mfe = future_high / close - 1.0
        long_mae = future_low / close - 1.0
        short_mfe = 1.0 - future_low / close
        short_mae = 1.0 - future_high / close
        out[f"raw_return_{label}"] = raw_ret
        out[f"aligned_return_{label}"] = aligned_ret
        out[f"mfe_{label}"] = np.where(out["aligned_position"] > 0, long_mfe, short_mfe)
        out[f"mae_{label}"] = np.where(out["aligned_position"] > 0, long_mae, short_mae)
        end_ns = ts_ns + int(pd.Timedelta(hours=bars).value)
        future_funding_sum = _funding_sum_between(ts_ns, end_ns, fts, prefix)
        # Long pays positive / receives negative; short receives positive / pays negative.
        out[f"funding_pnl_rate_{label}"] = -out["aligned_position"].to_numpy(dtype=float) * future_funding_sum
        out[f"aligned_return_plus_funding_{label}"] = aligned_ret + out[f"funding_pnl_rate_{label}"]
    return out


def summarize_states(panel: pd.DataFrame, momentum_hours: int, subset_name: str) -> pd.DataFrame:
    rows = []
    valid_states = ["A_NEG_MOM_POS_FUNDING", "B_POS_MOM_NEG_FUNDING", "C_POS_MOM_POS_FUNDING", "D_NEG_MOM_NEG_FUNDING"]
    for state in valid_states:
        g = panel[panel["state"] == state]
        for label in HORIZONS:
            aligned = g[f"aligned_return_{label}"].dropna()
            funding_effect = g.loc[aligned.index, f"funding_pnl_rate_{label}"] if len(aligned) else pd.Series(dtype=float)
            combined = g.loc[aligned.index, f"aligned_return_plus_funding_{label}"] if len(aligned) else pd.Series(dtype=float)
            rows.append({
                "subset": subset_name,
                "momentum_hours": momentum_hours,
                "state": state,
                "horizon": label,
                "observations": int(len(aligned)),
                "mean_aligned_return": float(aligned.mean()) if len(aligned) else np.nan,
                "median_aligned_return": float(aligned.median()) if len(aligned) else np.nan,
                "direction_hit_rate": float((aligned > 0).mean()) if len(aligned) else np.nan,
                "mean_mfe": float(g.loc[aligned.index, f"mfe_{label}"].mean()) if len(aligned) else np.nan,
                "mean_mae": float(g.loc[aligned.index, f"mae_{label}"].mean()) if len(aligned) else np.nan,
                "mean_funding_pnl_rate": float(funding_effect.mean()) if len(funding_effect) else np.nan,
                "mean_aligned_return_plus_funding": float(combined.mean()) if len(combined) else np.nan,
            })
    return pd.DataFrame(rows)


def summary_by_group(panel: pd.DataFrame, momentum_hours: int, group_col: str) -> pd.DataFrame:
    rows = []
    focus = panel[panel["state"].isin(["A_NEG_MOM_POS_FUNDING", "B_POS_MOM_NEG_FUNDING"])]
    for (state, group), g in focus.groupby(["state", group_col], dropna=False):
        aligned = g["aligned_return_24h"].dropna()
        rows.append({
            "momentum_hours": momentum_hours,
            "state": state,
            "group": str(group),
            "observations": int(len(aligned)),
            "mean_aligned_return_24h": float(aligned.mean()) if len(aligned) else np.nan,
            "median_aligned_return_24h": float(aligned.median()) if len(aligned) else np.nan,
            "hit_rate_24h": float((aligned > 0).mean()) if len(aligned) else np.nan,
            "mean_funding_pnl_rate_24h": float(g.loc[aligned.index, "funding_pnl_rate_24h"].mean()) if len(aligned) else np.nan,
        })
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Funding-Aligned Momentum relationship study; research only")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start-month", default="2019-09")
    parser.add_argument("--end-month", default="2026-06")
    parser.add_argument("--evaluation-start", default="2020-01-01T00:00:00Z")
    parser.add_argument("--output-dir", default="data/arena/funding_aligned_momentum_research")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    cfg = FundingAlignedMomentumResearchConfig(
        symbol=args.symbol,
        start_month=args.start_month,
        end_month=args.end_month,
        evaluation_start=args.evaluation_start,
    )
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    hourly = load_hourly_futures(cfg.symbol, cfg.start_month, cfg.end_month, args.workers)
    funding = fetch_funding(cfg.symbol, cfg.start_month, cfg.end_month)
    data = attach_known_funding(hourly, funding)
    data = add_regimes(data)
    start = pd.Timestamp(cfg.evaluation_start)
    data = data[data["timestamp"] >= start].copy().reset_index(drop=True)

    all_state_rows = []
    all_year_rows = []
    all_regime_rows = []
    horizon_comparison = []
    for momentum_hours in cfg.momentum_hours:
        panel = build_state_panel(data, funding, momentum_hours)
        panel.to_parquet(out / f"state_panel_{momentum_hours}h.parquet", index=False)
        all_state_rows.append(summarize_states(panel, momentum_hours, "full_2020_2026"))
        all_state_rows.append(summarize_states(panel[panel["timestamp"] >= pd.Timestamp("2024-01-01T00:00:00Z")], momentum_hours, "recent_2024_2026"))
        panel["year"] = panel["timestamp"].dt.year
        for year, group in panel.groupby("year"):
            block = summarize_states(group, momentum_hours, str(year))
            all_year_rows.append(block[block["horizon"] == "24h"])
        for col in ["trend_regime", "vol_regime", "funding_regime"]:
            block = summary_by_group(panel, momentum_hours, col)
            block["dimension"] = col
            all_regime_rows.append(block)

        focus = panel[panel["state"].isin(["A_NEG_MOM_POS_FUNDING", "B_POS_MOM_NEG_FUNDING"])]
        recent = focus[focus["timestamp"] >= pd.Timestamp("2024-01-01T00:00:00Z")]
        for state in ["A_NEG_MOM_POS_FUNDING", "B_POS_MOM_NEG_FUNDING"]:
            g = focus[focus["state"] == state]["aligned_return_24h"].dropna()
            r = recent[recent["state"] == state]["aligned_return_24h"].dropna()
            horizon_comparison.append({
                "momentum_hours": momentum_hours,
                "state": state,
                "full_observations": int(len(g)),
                "full_mean_aligned_return_24h": float(g.mean()) if len(g) else np.nan,
                "full_hit_rate_24h": float((g > 0).mean()) if len(g) else np.nan,
                "recent_observations": int(len(r)),
                "recent_mean_aligned_return_24h": float(r.mean()) if len(r) else np.nan,
                "recent_hit_rate_24h": float((r > 0).mean()) if len(r) else np.nan,
            })

    state_summary = pd.concat(all_state_rows, ignore_index=True)
    yearly = pd.concat(all_year_rows, ignore_index=True) if all_year_rows else pd.DataFrame()
    regimes = pd.concat(all_regime_rows, ignore_index=True) if all_regime_rows else pd.DataFrame()
    comparison = pd.DataFrame(horizon_comparison)
    state_summary.to_csv(out / "state_horizon_summary.csv", index=False)
    yearly.to_csv(out / "yearly_24h_summary.csv", index=False)
    regimes.to_csv(out / "regime_24h_summary.csv", index=False)
    comparison.to_csv(out / "momentum_horizon_comparison.csv", index=False)

    payload = {
        "config": asdict(cfg),
        "data": {
            "start": data["timestamp"].min().isoformat(),
            "end": data["timestamp"].max().isoformat(),
            "hourly_rows": int(len(data)),
            "funding_events": int(len(funding)),
            "source": "Binance Vision USD-M official public archives",
        },
        "lookahead_policy": "At each candle close, signal research uses only the latest funding payment whose timestamp is <= decision_timestamp. Future funding payments are outcome-only.",
        "parameter_optimization": False,
        "momentum_horizons_compared": [24, 168],
        "funding_sign_rule": "positive > 0, negative < 0",
        "live_trading": False,
        "actual_orders": False,
    }
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
