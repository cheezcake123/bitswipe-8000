from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from uuid import uuid4

import numpy as np
import pandas as pd

from strategy_arena.funding_carry_v1 import FundingCarryV1Config
from strategy_arena.market_store import AppendOnlyMarketStore

HISTORICAL_BACKTEST_CUTOFF_UTC = pd.Timestamp("2026-06-30T23:59:59.999Z")
FORWARD_OOS_START_UTC = pd.Timestamp("2026-07-01T00:00:00Z")
DEFAULT_LEAGUE_ROOT = "data/arena/forward_oos_league"
REGISTRY_FILENAME = "frozen_strategy_registry.json"
PROMOTION_GATES_FILENAME = "promotion_gates.json"


@dataclass(frozen=True)
class FrozenStrategyRecord:
    strategy_name: str
    strategy_version: str
    status: str
    verdict: str
    frozen_at: str
    parameters: dict
    allowed_for_selector: bool
    allowed_for_paper: bool
    activation: str | None
    implementation_ref: str
    notes: str

    @property
    def key(self) -> str:
        return f"{self.strategy_name}:{self.strategy_version}"

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "strategy_name": self.strategy_name,
                "strategy_version": self.strategy_version,
                "parameters": self.parameters,
                "implementation_ref": self.implementation_ref,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class FrozenStrategyRegistry:
    def __init__(self, records: Iterable[FrozenStrategyRecord]):
        self.records = {record.key: record for record in records}
        if len(self.records) != len(list(records)) if not isinstance(records, list) else len(self.records) != len(records):
            raise ValueError("Duplicate frozen strategy registry key")
        self._validate_policy()

    @classmethod
    def from_json(cls, path: str | Path) -> "FrozenStrategyRegistry":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        records = [FrozenStrategyRecord(**item) for item in payload["strategies"]]
        registry = cls(records)
        configured_start = pd.Timestamp(payload["forward_oos_start_utc"])
        if configured_start != FORWARD_OOS_START_UTC:
            raise ValueError("Forward OOS start is immutable and does not match code constant")
        return registry

    def _validate_policy(self) -> None:
        for record in self.records.values():
            if record.verdict in {"C", "pending_data"} and record.allowed_for_selector:
                raise ValueError(f"{record.key} cannot be selector-eligible with verdict={record.verdict}")
            if record.allowed_for_paper:
                raise ValueError(f"Paper eligibility must require explicit future user approval: {record.key}")
        selector_keys = [r.key for r in self.records.values() if r.allowed_for_selector]
        if selector_keys != ["funding_carry:v1"]:
            raise ValueError(f"Only funding_carry:v1 may currently be selector-eligible, got {selector_keys}")

    def selector_eligible(self) -> list[FrozenStrategyRecord]:
        return [r for r in self.records.values() if r.allowed_for_selector]

    def shadow_strategies(self) -> list[FrozenStrategyRecord]:
        return [r for r in self.records.values() if not r.allowed_for_selector]


@dataclass(frozen=True)
class RegimeSnapshot:
    timestamp: pd.Timestamp
    trend: str
    volatility: str
    funding: str
    uncertain: bool
    tags: tuple[str, ...]
    seven_day_return: float | None
    realized_volatility_24h: float | None
    volatility_reference: float | None
    funding_rate: float | None


class RegimeDetectorV1:
    """Backward-only, explainable multi-label regime detector.

    Trend: trailing 7d return, Bull >= +5%, Bear <= -5%, otherwise Sideways.
    Volatility: trailing 24h realized volatility versus the median of prior 30d values.
    Funding: High Positive >= frozen Funding Carry +2bp/event, Negative < 0, else Normal.
    """

    def detect(self, hourly: pd.DataFrame) -> RegimeSnapshot:
        if hourly.empty:
            raise ValueError("hourly market history is empty")
        data = hourly.copy().sort_values("timestamp").reset_index(drop=True)
        ts = pd.Timestamp(data.iloc[-1]["timestamp"])
        close = pd.to_numeric(data["close"], errors="coerce")
        funding = pd.to_numeric(data.get("funding_rate", pd.Series(index=data.index, dtype=float)), errors="coerce")

        ret7d = float(close.iloc[-1] / close.iloc[-169] - 1.0) if len(data) >= 169 and close.iloc[-169] > 0 else None
        if ret7d is None:
            trend = "UNKNOWN"
        elif ret7d >= 0.05:
            trend = "BULL"
        elif ret7d <= -0.05:
            trend = "BEAR"
        else:
            trend = "SIDEWAYS"

        logret = np.log(close / close.shift(1))
        rv24 = logret.rolling(24, min_periods=24).std(ddof=0) * math.sqrt(24)
        rv_ref = rv24.shift(1).rolling(30 * 24, min_periods=7 * 24).median()
        current_rv = float(rv24.iloc[-1]) if pd.notna(rv24.iloc[-1]) else None
        current_ref = float(rv_ref.iloc[-1]) if pd.notna(rv_ref.iloc[-1]) else None
        if current_rv is None or current_ref is None:
            vol = "UNKNOWN"
        else:
            vol = "HIGH_VOLATILITY" if current_rv >= current_ref else "LOW_VOLATILITY"

        current_funding = float(funding.iloc[-1]) if len(funding) and pd.notna(funding.iloc[-1]) else None
        if current_funding is None:
            funding_regime = "UNKNOWN"
        elif current_funding >= FundingCarryV1Config().entry_funding_rate:
            funding_regime = "HIGH_POSITIVE_FUNDING"
        elif current_funding < 0:
            funding_regime = "NEGATIVE_FUNDING"
        else:
            funding_regime = "NORMAL_FUNDING"

        uncertain = "UNKNOWN" in {trend, vol, funding_regime}
        tags = tuple(x for x in (trend, vol, funding_regime, "UNCERTAIN" if uncertain else None) if x)
        return RegimeSnapshot(ts, trend, vol, funding_regime, uncertain, tags, ret7d, current_rv, current_ref, current_funding)


@dataclass(frozen=True)
class PositionRequest:
    strategy_name: str
    strategy_version: str
    spot_btc: float = 0.0
    perpetual_btc: float = 0.0
    margin_usage: float = 0.0
    active: bool = True
    reason: str = ""


class ConflictManager:
    """Detection only. It never resolves conflicts or changes strategy signals."""

    @staticmethod
    def analyze(requests: Iterable[PositionRequest]) -> dict:
        active = [r for r in requests if r.active]
        spot = float(sum(r.spot_btc for r in active))
        perp = float(sum(r.perpetual_btc for r in active))
        same_direction: list[dict] = []
        opposite: list[dict] = []
        for i, left in enumerate(active):
            for right in active[i + 1 :]:
                for leg, a, b in (
                    ("SPOT", left.spot_btc, right.spot_btc),
                    ("PERPETUAL", left.perpetual_btc, right.perpetual_btc),
                ):
                    if a == 0 or b == 0:
                        continue
                    row = {"leg": leg, "left": left.strategy_name, "right": right.strategy_name}
                    if np.sign(a) == np.sign(b):
                        same_direction.append(row)
                    else:
                        opposite.append(row)
        return {
            "strategy_requests": [asdict(r) for r in active],
            "spot_exposure_btc": spot,
            "perpetual_exposure_btc": perp,
            "net_btc_delta": spot + perp,
            "gross_exposure_btc": float(sum(abs(r.spot_btc) + abs(r.perpetual_btc) for r in active)),
            "same_direction_duplicates": same_direction,
            "opposite_direction_conflicts": opposite,
            "margin_usage": float(sum(max(0.0, r.margin_usage) for r in active)),
            "has_conflict": bool(opposite),
        }


class StrategySelectorFoundation:
    """Rule-based eligibility only. No AI selection, capital allocation, orders, or auto-promotion."""

    def __init__(self, registry: FrozenStrategyRegistry):
        self.registry = registry

    def evaluate(self, regime: RegimeSnapshot, risk_gate_passed: bool) -> list[dict]:
        decisions = []
        for record in self.registry.records.values():
            eligible = False
            reason = "registry_not_allowed_for_selector"
            if record.allowed_for_selector:
                if record.key == "funding_carry:v1":
                    if regime.funding != "HIGH_POSITIVE_FUNDING":
                        reason = "funding_carry_requires_frozen_2bp_high_positive_funding"
                    elif not risk_gate_passed:
                        reason = "risk_gate_failed"
                    else:
                        eligible = True
                        reason = "eligible_hypothetical_only"
                else:
                    reason = "no_selector_rule_registered"
            decisions.append({
                "timestamp": regime.timestamp,
                "strategy_name": record.strategy_name,
                "strategy_version": record.strategy_version,
                "eligible": eligible,
                "reason": reason,
                "hypothetical_only": True,
                "paper_order": False,
                "live_order": False,
            })
        return decisions


class AppendOnlyLeagueStore:
    """Immutable Parquet batches for forward-only observations and hypothetical results."""

    DATASETS = {"signals", "hypothetical_fills", "hypothetical_trades", "selector_decisions", "overlap", "scorecards"}

    def __init__(self, root: str | Path = DEFAULT_LEAGUE_ROOT):
        self.root = Path(root)
        self._ensure_metadata()

    def _ensure_metadata(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / "league_metadata.json"
        expected = {
            "historical_backtest_cutoff_utc": HISTORICAL_BACKTEST_CUTOFF_UTC.isoformat(),
            "forward_oos_start_utc": FORWARD_OOS_START_UTC.isoformat(),
            "immutable_start": True,
            "orders_enabled": False,
            "paper_trading_enabled": False,
            "auto_promotion_enabled": False,
        }
        if target.exists():
            current = json.loads(target.read_text(encoding="utf-8"))
            if current.get("forward_oos_start_utc") != expected["forward_oos_start_utc"]:
                raise RuntimeError("Forward OOS start cannot be changed after initialization")
        else:
            target.write_text(json.dumps(expected, indent=2), encoding="utf-8")

    def append(self, dataset: str, rows: list[dict]) -> Path | None:
        if dataset not in self.DATASETS:
            raise ValueError(f"Unknown league dataset: {dataset}")
        if not rows:
            return None
        frame = pd.DataFrame(rows).copy()
        if "timestamp" not in frame.columns:
            frame["timestamp"] = pd.Timestamp(datetime.now(timezone.utc)).floor("ms")
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True).astype("datetime64[ms, UTC]")
        if dataset in {"signals", "hypothetical_fills", "hypothetical_trades", "selector_decisions"}:
            if (frame["timestamp"] < FORWARD_OOS_START_UTC).any():
                raise ValueError("Historical/backtest data cannot be written into Forward OOS League")
        frame["recorded_at_utc"] = pd.Timestamp(datetime.now(timezone.utc)).floor("ms")
        frame["forward_oos_start_utc"] = FORWARD_OOS_START_UTC
        day = frame["timestamp"].min().strftime("%Y-%m-%d")
        directory = self.root / dataset / f"date={day}"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"part-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex[:10]}.parquet"
        temp = target.with_suffix(".tmp")
        frame.to_parquet(temp, index=False)
        temp.replace(target)
        return target

    def read(self, dataset: str) -> pd.DataFrame:
        if dataset not in self.DATASETS:
            raise ValueError(f"Unknown league dataset: {dataset}")
        files = sorted((self.root / dataset).rglob("part-*.parquet")) if (self.root / dataset).exists() else []
        if not files:
            return pd.DataFrame()
        data = pd.concat([pd.read_parquet(path) for path in files], ignore_index=True)
        if "timestamp" in data.columns:
            data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
            data = data.sort_values("timestamp")
        return data.reset_index(drop=True)


class SignalOverlapAnalyzer:
    ACTIVE = {"LONG", "SHORT", "PAIR_LONG_SPOT_SHORT_PERP", "ELIGIBLE"}

    @classmethod
    def summarize(cls, signals: pd.DataFrame) -> pd.DataFrame:
        if signals.empty:
            return pd.DataFrame(columns=["strategy_a", "strategy_b", "same_timestamp_rate", "same_direction_rate", "opposite_direction_rate", "a_subset_of_b_rate"])
        data = signals.copy()
        data = data[data["signal"].isin(cls.ACTIVE)]
        strategies = sorted(data["strategy_name"].unique())
        rows = []
        for i, a in enumerate(strategies):
            arows = data[data["strategy_name"] == a][["timestamp", "signal"]].drop_duplicates("timestamp")
            aset = set(arows["timestamp"])
            for b in strategies[i + 1 :]:
                brows = data[data["strategy_name"] == b][["timestamp", "signal"]].drop_duplicates("timestamp")
                bset = set(brows["timestamp"])
                inter = aset & bset
                same = opposite = 0
                if inter:
                    am = arows.set_index("timestamp")["signal"]
                    bm = brows.set_index("timestamp")["signal"]
                    for ts in inter:
                        sa, sb = am.loc[ts], bm.loc[ts]
                        da = 1 if "LONG" in sa and "SHORT_PERP" not in sa else -1 if "SHORT" in sa else 0
                        db = 1 if "LONG" in sb and "SHORT_PERP" not in sb else -1 if "SHORT" in sb else 0
                        if da and db and da == db:
                            same += 1
                        elif da and db and da != db:
                            opposite += 1
                denom_inter = len(inter) or 1
                rows.append({
                    "strategy_a": a,
                    "strategy_b": b,
                    "same_timestamp_rate": len(inter) / len(aset | bset) if aset | bset else 0.0,
                    "same_direction_rate": same / denom_inter,
                    "opposite_direction_rate": opposite / denom_inter,
                    "a_subset_of_b_rate": len(inter) / len(aset) if aset else 0.0,
                    "sample_a": len(aset),
                    "sample_b": len(bset),
                })
        return pd.DataFrame(rows)


class ForwardScorecard:
    @staticmethod
    def from_trades(trades: pd.DataFrame, initial_equity: float = 10_000.0) -> dict:
        if trades.empty:
            return {
                "sample_size": 0,
                "number_of_hypothetical_trades": 0,
                "gross_return": 0.0,
                "net_return": 0.0,
                "maximum_drawdown": 0.0,
                "sharpe": None,
                "profit_factor": None,
                "win_rate": None,
                "fee": 0.0,
                "slippage": 0.0,
                "funding_net": 0.0,
                "average_holding_hours": 0.0,
                "note": "Insufficient Forward OOS sample; risk-adjusted metrics must not be over-interpreted.",
            }
        net = pd.to_numeric(trades["net_pnl"], errors="coerce").fillna(0.0)
        gross = pd.to_numeric(trades.get("gross_pnl", trades.get("pre_cost_gross_pnl", net)), errors="coerce").fillna(0.0)
        equity = initial_equity + net.cumsum()
        peak = equity.cummax()
        mdd = float(((equity / peak) - 1.0).min()) if len(equity) else 0.0
        wins = float(net[net > 0].sum())
        losses = float(-net[net < 0].sum())
        pf = None if losses == 0 else wins / losses
        std = float(net.std(ddof=0))
        sharpe = None if len(net) < 20 or std == 0 else float(net.mean() / std * math.sqrt(len(net)))
        return {
            "sample_size": int(len(trades)),
            "number_of_hypothetical_trades": int(len(trades)),
            "gross_return": float(gross.sum() / initial_equity),
            "net_return": float(net.sum() / initial_equity),
            "maximum_drawdown": mdd,
            "sharpe": sharpe,
            "profit_factor": pf,
            "win_rate": float((net > 0).mean()),
            "fee": float(pd.to_numeric(trades.get("fee_cost", 0.0), errors="coerce").sum()),
            "slippage": float(pd.to_numeric(trades.get("slippage_cost", 0.0), errors="coerce").sum()),
            "funding_net": float((pd.to_numeric(trades.get("funding_income", 0.0), errors="coerce").sum() - pd.to_numeric(trades.get("funding_cost", 0.0), errors="coerce").sum())),
            "average_holding_hours": float(pd.to_numeric(trades.get("holding_hours", 0.0), errors="coerce").mean()),
            "note": "Sharpe/PF require sample-size context; no automatic promotion is permitted.",
        }


def collector_health_snapshot(store: AppendOnlyMarketStore) -> dict:
    inventory = store.inventory()
    rows = []
    now = pd.Timestamp.now(tz="UTC")
    for item in inventory.to_dict("records"):
        last = item.get("last_timestamp")
        age_minutes = None
        if last is not None and not pd.isna(last):
            age_minutes = float((now - pd.Timestamp(last)) / pd.Timedelta(minutes=1))
        rows.append({**item, "age_minutes": age_minutes})
    liquidation = next((r for r in rows if r["dataset"] == "liquidation"), None)
    return {
        "process_status": "UNVERIFIED_REQUIRES_SERVER_SYSTEMD_ACCESS",
        "inventory": rows,
        "liquidation_websocket_status": "UNVERIFIED" if not liquidation or not liquidation.get("rows") else "DATA_PRESENT_BUT_SOCKET_LIVENESS_UNVERIFIED",
        "note": "Repository access cannot prove whether the production systemd collector process is currently running.",
    }


def load_default_registry() -> FrozenStrategyRegistry:
    path = Path(__file__).with_name(REGISTRY_FILENAME)
    return FrozenStrategyRegistry.from_json(path)


def run_foundation_snapshot(store_root: str, league_root: str) -> dict:
    registry = load_default_registry()
    league = AppendOnlyLeagueStore(league_root)
    store = AppendOnlyMarketStore(store_root)
    health = collector_health_snapshot(store)
    selector = StrategySelectorFoundation(registry)

    futures = store.read("futures_ohlcv", symbol="BTCUSDT")
    funding = store.read("funding_rate", symbol="BTCUSDT")
    if futures.empty:
        result = {
            "forward_oos_start_utc": FORWARD_OOS_START_UTC.isoformat(),
            "forward_rows_available": 0,
            "selector_decisions_written": 0,
            "collector_health": health,
            "orders_enabled": False,
        }
    else:
        futures = futures.copy().sort_values("timestamp").drop_duplicates("timestamp")
        futures["timestamp"] = pd.to_datetime(futures["timestamp"], utc=True)
        futures = futures.set_index("timestamp").resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index()
        futures["decision_timestamp"] = futures["timestamp"] + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1)
        if funding.empty:
            futures["funding_rate"] = np.nan
        else:
            f = funding[["timestamp", "funding_rate"]].copy().sort_values("timestamp")
            f["timestamp"] = pd.to_datetime(f["timestamp"], utc=True)
            futures = pd.merge_asof(futures.sort_values("decision_timestamp"), f.rename(columns={"timestamp": "funding_timestamp"}), left_on="decision_timestamp", right_on="funding_timestamp", direction="backward", allow_exact_matches=True)
        forward = futures[futures["timestamp"] >= FORWARD_OOS_START_UTC].copy()
        if forward.empty:
            result = {
                "forward_oos_start_utc": FORWARD_OOS_START_UTC.isoformat(),
                "forward_rows_available": 0,
                "selector_decisions_written": 0,
                "collector_health": health,
                "orders_enabled": False,
            }
        else:
            regime = RegimeDetectorV1().detect(futures[futures["timestamp"] <= forward.iloc[-1]["timestamp"]])
            decisions = selector.evaluate(regime, risk_gate_passed=False)
            league.append("selector_decisions", decisions)
            result = {
                "forward_oos_start_utc": FORWARD_OOS_START_UTC.isoformat(),
                "forward_rows_available": int(len(forward)),
                "latest_forward_timestamp": pd.Timestamp(forward.iloc[-1]["timestamp"]).isoformat(),
                "latest_regime": asdict(regime),
                "selector_decisions_written": len(decisions),
                "collector_health": health,
                "orders_enabled": False,
                "note": "Risk gate defaults to false in foundation snapshot; eligibility is observational only.",
            }
    target = Path(league_root) / "latest_foundation_snapshot.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Forward OOS League + Strategy Selector Foundation; no orders")
    parser.add_argument("--store-root", default="data/arena/market_store")
    parser.add_argument("--league-root", default=DEFAULT_LEAGUE_ROOT)
    args = parser.parse_args()
    print(json.dumps(run_foundation_snapshot(args.store_root, args.league_root), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
