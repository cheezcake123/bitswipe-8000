from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from strategy_arena.backtest import BacktestResult


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def save_result(result: BacktestResult, output_dir: str | Path, label: str) -> Path:
    out = Path(output_dir) / result.strategy_name / result.strategy_version / label
    out.mkdir(parents=True, exist_ok=True)

    equity = result.equity_curve.copy()
    equity.to_csv(out / "equity_curve.csv", index=False)
    result.trades.to_csv(out / "trades.csv", index=False)
    result.signals.to_csv(out / "signals.csv", index=False)

    if not equity.empty:
        peak = equity["equity"].cummax()
        drawdown = equity[["timestamp"]].copy()
        drawdown["drawdown"] = equity["equity"] / peak - 1.0
        drawdown.to_csv(out / "drawdown_curve.csv", index=False)

        try:
            import plotly.express as px

            fig = px.line(equity, x="timestamp", y="equity", title=f"{result.strategy_name} {result.strategy_version} - Equity Curve")
            fig.write_html(out / "equity_curve.html", include_plotlyjs="cdn")

            fig = px.line(drawdown, x="timestamp", y="drawdown", title=f"{result.strategy_name} {result.strategy_version} - Drawdown Curve")
            fig.write_html(out / "drawdown_curve.html", include_plotlyjs="cdn")
        except Exception:
            pass

    with open(out / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(_json_safe(result.metrics), f, ensure_ascii=False, indent=2, allow_nan=False)
    return out


def save_comparison(rows: list[dict], output_dir: str | Path) -> Path:
    target = Path(output_dir) / "strategy_comparison.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(target, index=False)
    return target
