from __future__ import annotations

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
        materialized = list(records)
        self.records = {record.key: record for record in materialized}
        if len(self.records) != len(materialized):
            raise ValueError("Duplicate frozen strategy registry key")
        self._validate_policy()

    @classmethod
    def from_json(cls, path: str | Path) -> "FrozenStrategyRegistry":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if pd.Timestamp(payload["forward_oos_start_utc"]) != FORWARD_OOS_START_UTC:
            raise ValueError("Forward OOS start is immutable and does not match code constant")
        return cls(FrozenStrategyRecord(**item) for item in payload["strategies"])

    def _validate_policy(self) -> None:
        for record in self.records.values():
            if record.verdict in {"C", "pending_data"} and record.allowed_for_selector:
                raise ValueError(f"{record.key} cannot be selector-eligible with verdict={record.verdict}")
            if record.allowed_for_paper:
                raise ValueError(f"Paper eligibility requires future explicit user approval: {record.key}")
        allowed = [record.key for record in self.records.values() if record.allowed_for_selector]
        if allowed != ["funding_carry:v1"]:
            raise ValueError(f"Only funding_carry:v1 may currently be selector-eligible, got {allowed}")

    def selector_eligible(self) -> list[FrozenStrategyRecord]:
        return [record for record in self.records.values() if record.allowed_for_selector]

    def shadow_strategies(self) -> list[FrozenStrategyRecord]:
        return [record for record in self.records.values() if not record.allowed_for_selector]


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
    """Explainable, backward-only multi-label regime detector."""

    def detect(self, hourly: pd.DataFrame) -> RegimeSnapshot:
        if hourly.empty:
            raise ValueError("hourly market history is empty")
        data = hourly.copy().sort_values("timestamp").reset_index(drop=True)
        ts = pd.Timestamp(data.iloc[-1]["timestamp"])
        close = pd.to_numeric(data["close"], errors="coerce")
        funding = pd.to_numeric(
            data.get("funding_rate", pd.Series(index=data.index, dtype=float)),
            errors="coerce",
        )

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
            volatility = "UNKNOWN"
        else:
            volatility = "HIGH_VOLATILITY" if current_rv >= current_ref else "LOW_VOLATILITY"

        current_funding = float(funding.iloc[-1]) if len(funding) and pd.notna(funding.iloc[-1]) else None
        if current_funding is None:
            funding_regime = "UNKNOWN"
        elif current_funding >= FundingCarryV1Config().entry_funding_rate:
            funding_regime = "HIGH_POSITIVE_FUNDING"
        elif current_funding < 0:
            funding_regime = "NEGATIVE_FUNDING"
        else:
            funding_regime = "NORMAL_FUNDING"

        uncertain = "UNKNOWN" in {trend, volatility, funding_regime}
        tags = tuple(tag for tag in (trend, volatility, funding_regime, "UNCERTAIN" if uncertain else None) if tag)
        return RegimeSnapshot(
            timestamp=ts,
            trend=trend,
            volatility=volatility,
            funding=funding_regime,
            uncertain=uncertain,
            tags=tags,
            seven_day_return=ret7d,
            realized_volatility_24h=current_rv,
            volatility_reference=current_ref,
            funding_rate=current_funding,
        )


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
    """Detect conflicts and duplicate exposure. Never resolves or changes a strategy signal."""

    @staticmethod
    def analyze(requests: Iterable[PositionRequest]) -> dict:
        active = [request for request in requests if request.active]
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
                    item = {"leg": leg, "left": left.strategy_name, "right": right.strategy_name}
                    if np.sign(a) == np.sign(b):
                        same_direction.append(item)
                    else:
                        opposite.append(item)
        spot = float(sum(request.spot_btc for request in active))
        perpetual = float(sum(request.perpetual_btc for request in active))
        return {
            "strategy_requests": [asdict(request) for request in active],
            "spot_exposure_btc": spot,
            "perpetual_exposure_btc": perpetual,
            "net_btc_delta": spot + perpetual,
            "gross_exposure_btc": float(sum(abs(r.spot_btc) + abs(r.perpetual_btc) for r in active)),
            "same_direction_duplicates": same_direction,
            "opposite_direction_conflicts": opposite,
            "margin_usage": float(sum(max(0.0, r.margin_usage) for r in active)),
            "has_conflict": bool(opposite),
        }


class StrategySelectorFoundation:
    """Rules-only eligibility. No AI choice, allocation, order, Paper start, or promotion."""

    def __init__(self, registry: FrozenStrategyRegistry):
        self.registry = registry

    def evaluate(self, regime: RegimeSnapshot, risk_gate_passed: bool) -> list[dict]:
        rows = []
        for record in self.registry.records.values():
            eligible = False
            reason = "registry_not_allowed_for_selector"
            if record.allowed_for_selector and record.key == "funding_carry:v1":
                if regime.funding != "HIGH_POSITIVE_FUNDING":
                    reason = "funding_carry_requires_frozen_2bp_high_positive_funding"
                elif not risk_gate_passed:
                    reason = "risk_gate_failed"
                else:
                    eligible = True
                    reason = "eligible_hypothetical_only"
            rows.append(
                {
                    "timestamp": regime.timestamp,
                    "strategy_name": record.strategy_name,
                    "strategy_version": record.strategy_version,
                    "eligible": eligible,
                    "reason": reason,
                    "hypothetical_only": True,
                    "paper_order": False,
                    "live_order": False,
                }
            )
        return rows


class AppendOnlyLeagueStore:
    """Immutable Parquet batches for forward-only supervision records."""

    DATASETS = {
        "signals",
        "hypothetical_fills",
        "hypothetical_trades",
        "selector_decisions",
        "conflicts",
        "overlap",
        "scorecards",
    }

    def __init__(self, root: str | Path = DEFAULT_LEAGUE_ROOT):
        self.root = Path(root)
        self._ensure_metadata()

    def _ensure_metadata(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "league_metadata.json"
        expected = {
            "historical_backtest_cutoff_utc": HISTORICAL_BACKTEST_CUTOFF_UTC.isoformat(),
            "forward_oos_start_utc": FORWARD_OOS_START_UTC.isoformat(),
            "immutable_start": True,
            "orders_enabled": False,
            "paper_trading_enabled": False,
            "auto_promotion_enabled": False,
        }
        if path.exists():
            current = json.loads(path.read_text(encoding="utf-8"))
            if current.get("forward_oos_start_utc") != expected["forward_oos_start_utc"]:
                raise RuntimeError("Forward OOS start cannot be changed after initialization")
        else:
            path.write_text(json.dumps(expected, indent=2), encoding="utf-8")

    def append(self, dataset: str, rows: list[dict]) -> Path | None:
        if dataset not in self.DATASETS:
            raise ValueError(f"Unknown league dataset: {dataset}")
        if not rows:
            return None
        frame = pd.DataFrame(rows).copy()
        if "timestamp" not in frame.columns:
            frame["timestamp"] = pd.Timestamp(datetime.now(timezone.utc)).floor("ms")
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True).astype("datetime64[ms, UTC]")
        if dataset in {
            "signals",
            "hypothetical_fills",
            "hypothetical_trades",
            "selector_decisions",
            "conflicts",
            "overlap",
            "scorecards",
        } and (frame["timestamp"] < FORWARD_OOS_START_UTC).any():
            raise ValueError("Historical/backtest rows cannot be written into Forward OOS League")
        frame["recorded_at_utc"] = pd.Timestamp(datetime.now(timezone.utc)).floor("ms")
        frame["forward_oos_start_utc"] = FORWARD_OOS_START_UTC
        day = frame["timestamp"].min().strftime("%Y-%m-%d")
        directory = self.root / dataset / f"date={day}"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"part-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex[:10]}.parquet"
        temporary = target.with_suffix(".tmp")
        frame.to_parquet(temporary, index=False)
        temporary.replace(target)
        return target

    def read(self, dataset: str) -> pd.DataFrame:
        if dataset not in self.DATASETS:
            raise ValueError(f"Unknown league dataset: {dataset}")
        base = self.root / dataset
        files = sorted(base.rglob("part-*.parquet")) if base.exists() else []
        if not files:
            return pd.DataFrame()
        data = pd.concat([pd.read_parquet(path) for path in files], ignore_index=True)
        if "timestamp" in data.columns:
            data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
            data = data.sort_values("timestamp")
        return data.reset_index(drop=True)


class SignalOverlapAnalyzer:
    ACTIVE_SIGNALS = {"LONG", "SHORT", "PAIR_LONG_SPOT_SHORT_PERP", "ELIGIBLE"}

    @classmethod
    def summarize(cls, signals: pd.DataFrame) -> pd.DataFrame:
        columns = [
            "strategy_a",
            "strategy_b",
            "same_timestamp_rate",
            "same_direction_rate",
            "opposite_direction_rate",
            "a_subset_of_b_rate",
            "sample_a",
            "sample_b",
        ]
        if signals.empty:
            return pd.DataFrame(columns=columns)
        data = signals[signals["signal"].isin(cls.ACTIVE_SIGNALS)].copy()
        strategies = sorted(data["strategy_name"].unique())
        rows = []
        for i, strategy_a in enumerate(strategies):
            a = data[data["strategy_name"] == strategy_a][["timestamp", "signal"]].drop_duplicates("timestamp")
            aset = set(a["timestamp"])
            for strategy_b in strategies[i + 1 :]:
                b = data[data["strategy_name"] == strategy_b][["timestamp", "signal"]].drop_duplicates("timestamp")
                bset = set(b["timestamp"])
                intersection = aset & bset
                same = opposite = 0
                if intersection:
                    amap = a.set_index("timestamp")["signal"]
                    bmap = b.set_index("timestamp")["signal"]
                    for ts in intersection:
                        sa, sb = str(amap.loc[ts]), str(bmap.loc[ts])
                        da = -1 if sa == "SHORT" else 1 if sa == "LONG" else 0
                        db = -1 if sb == "SHORT" else 1 if sb == "LONG" else 0
                        if da and db and da == db:
                            same += 1
                        elif da and db and da != db:
                            opposite += 1
                rows.append(
                    {
                        "strategy_a": strategy_a,
                        "strategy_b": strategy_b,
                        "same_timestamp_rate": len(intersection) / len(aset | bset) if aset | bset else 0.0,
                        "same_direction_rate": same / len(intersection) if intersection else 0.0,
                        "opposite_direction_rate": opposite / len(intersection) if intersection else 0.0,
                        "a_subset_of_b_rate": len(intersection) / len(aset) if aset else 0.0,
                        "sample_a": len(aset),
                        "sample_b": len(bset),
                    }
                )
        return pd.DataFrame(rows, columns=columns)


class ForwardScorecard:
    @staticmethod
    def _sum_column(frame: pd.DataFrame, name: str) -> float:
        if name not in frame.columns:
            return 0.0
        return float(pd.to_numeric(frame[name], errors="coerce").fillna(0.0).sum())

    @classmethod
    def from_trades(cls, trades: pd.DataFrame, initial_equity: float = 10_000.0) -> dict:
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
        gross_column = "gross_pnl" if "gross_pnl" in trades.columns else "pre_cost_gross_pnl"
        gross = pd.to_numeric(trades[gross_column], errors="coerce").fillna(0.0) if gross_column in trades.columns else net
        equity = initial_equity + net.cumsum()
        drawdown = equity / equity.cummax() - 1.0
        wins = float(net[net > 0].sum())
        losses = float(-net[net < 0].sum())
        standard_deviation = float(net.std(ddof=0))
        return {
            "sample_size": int(len(trades)),
            "number_of_hypothetical_trades": int(len(trades)),
            "gross_return": float(gross.sum() / initial_equity),
            "net_return": float(net.sum() / initial_equity),
            "maximum_drawdown": float(drawdown.min()),
            "sharpe": None if len(net) < 20 or standard_deviation == 0 else float(net.mean() / standard_deviation * math.sqrt(len(net))),
            "profit_factor": None if losses == 0 else wins / losses,
            "win_rate": float((net > 0).mean()),
            "fee": cls._sum_column(trades, "fee_cost"),
            "slippage": cls._sum_column(trades, "slippage_cost"),
            "funding_net": cls._sum_column(trades, "funding_income") - cls._sum_column(trades, "funding_cost"),
            "average_holding_hours": float(pd.to_numeric(trades["holding_hours"], errors="coerce").mean()) if "holding_hours" in trades.columns else 0.0,
            "note": "Sharpe/PF require sample-size context; no automatic promotion is permitted.",
        }


def collector_health_snapshot(store: AppendOnlyMarketStore) -> dict:
    inventory = store.inventory()
    now = pd.Timestamp.now(tz="UTC")
    rows = []
    for item in inventory.to_dict("records"):
        last = item.get("last_timestamp")
        age_minutes = None
        if last is not None and not pd.isna(last):
            age_minutes = float((now - pd.Timestamp(last)) / pd.Timedelta(minutes=1))
        rows.append({**item, "age_minutes": age_minutes})
    liquidation = next((row for row in rows if row["dataset"] == "liquidation"), None)
    return {
        "process_status": "UNVERIFIED_REQUIRES_SERVER_SYSTEMD_ACCESS",
        "inventory": rows,
        "liquidation_websocket_status": "UNVERIFIED" if not liquidation or not liquidation.get("rows") else "DATA_PRESENT_BUT_SOCKET_LIVENESS_UNVERIFIED",
        "note": "Repository access cannot prove whether the production systemd collector process is currently running.",
    }


def load_default_registry() -> FrozenStrategyRegistry:
    return FrozenStrategyRegistry.from_json(Path(__file__).with_name(REGISTRY_FILENAME))
