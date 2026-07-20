from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from uuid import uuid4

import pandas as pd


BASE_KEY = ("timestamp", "symbol", "source")


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    cadence: str
    description: str
    dedup_columns: tuple[str, ...] = BASE_KEY


DATASET_SPECS: dict[str, DatasetSpec] = {
    "futures_ohlcv": DatasetSpec("futures_ohlcv", "1m", "USD-M perpetual OHLCV at native research collection interval"),
    "spot_ohlcv": DatasetSpec("spot_ohlcv", "1m", "Spot OHLCV at native research collection interval"),
    "funding_rate": DatasetSpec("funding_rate", "event", "Actual funding events; do not resample on write"),
    "open_interest": DatasetSpec("open_interest", "5m", "Open-interest snapshots"),
    "liquidation": DatasetSpec(
        "liquidation",
        "event",
        "Market-wide forced-order stream events",
        dedup_columns=("timestamp", "symbol", "source", "event_id"),
    ),
    "perpetual_price": DatasetSpec("perpetual_price", "1m", "Perpetual last-traded price snapshot"),
    "spot_price": DatasetSpec("spot_price", "1m", "Spot last-traded price snapshot"),
    "mark_price": DatasetSpec("mark_price", "1m", "Perpetual mark-price snapshot"),
    "index_price": DatasetSpec("index_price", "1m", "Perpetual index-price snapshot"),
}


class AppendOnlyMarketStore:
    """Immutable Parquet-batch store.

    Existing parquet files are never modified or deleted by this class. New data is
    normalized to UTC, de-duplicated against already persisted keys, and written as
    a new immutable batch under dataset/source/symbol/date partitions.
    """

    def __init__(self, root: str | Path = "data/arena/market_store"):
        self.root = Path(root)

    def _spec(self, dataset: str) -> DatasetSpec:
        try:
            return DATASET_SPECS[dataset]
        except KeyError as exc:
            raise ValueError(f"Unknown market dataset: {dataset}") from exc

    @staticmethod
    def _safe_part(value: str) -> str:
        value = str(value).strip()
        if not value or any(ch in value for ch in ("/", "\\", "..")):
            raise ValueError(f"Unsafe partition value: {value!r}")
        return value

    @staticmethod
    def _normalize_timestamp(series: pd.Series) -> pd.Series:
        return pd.to_datetime(series, utc=True).astype("datetime64[ms, UTC]")

    def _partition_dir(self, dataset: str, source: str, symbol: str, day: str) -> Path:
        return (
            self.root
            / self._safe_part(dataset)
            / f"source={self._safe_part(source)}"
            / f"symbol={self._safe_part(symbol)}"
            / f"date={day}"
        )

    def _existing_keys(self, partition: Path, key_columns: tuple[str, ...]) -> set[tuple]:
        keys: set[tuple] = set()
        if not partition.exists():
            return keys
        for file in sorted(partition.glob("part-*.parquet")):
            frame = pd.read_parquet(file, columns=list(key_columns))
            if "timestamp" in frame.columns:
                frame["timestamp"] = self._normalize_timestamp(frame["timestamp"])
            keys.update(frame[list(key_columns)].itertuples(index=False, name=None))
        return keys

    def append(self, dataset: str, frame: pd.DataFrame) -> dict[str, object]:
        spec = self._spec(dataset)
        if frame.empty:
            return {"dataset": dataset, "received": 0, "written": 0, "duplicates": 0, "files": []}

        missing = [c for c in spec.dedup_columns if c not in frame.columns]
        if missing:
            raise ValueError(f"{dataset} missing dedup columns: {missing}")
        required = ["timestamp", "symbol", "source"]
        missing_required = [c for c in required if c not in frame.columns]
        if missing_required:
            raise ValueError(f"{dataset} missing required columns: {missing_required}")

        data = frame.copy()
        data["timestamp"] = self._normalize_timestamp(data["timestamp"])
        data["symbol"] = data["symbol"].astype(str).str.upper()
        data["source"] = data["source"].astype(str)
        data["ingested_at_utc"] = pd.Timestamp(datetime.now(timezone.utc)).floor("ms")
        data = data.sort_values("timestamp").drop_duplicates(list(spec.dedup_columns), keep="first")
        data["_day"] = data["timestamp"].dt.strftime("%Y-%m-%d")

        received = len(frame)
        written = 0
        files: list[str] = []
        duplicate_count = received - len(data)

        for (source, symbol, day), group in data.groupby(["source", "symbol", "_day"], sort=True):
            partition = self._partition_dir(dataset, source, symbol, day)
            existing = self._existing_keys(partition, spec.dedup_columns)
            keys = list(group[list(spec.dedup_columns)].itertuples(index=False, name=None))
            keep_mask = [key not in existing for key in keys]
            fresh = group.loc[keep_mask].drop(columns=["_day"])
            duplicate_count += len(group) - len(fresh)
            if fresh.empty:
                continue

            partition.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            target = partition / f"part-{stamp}-{uuid4().hex[:10]}.parquet"
            temp = target.with_suffix(".tmp")
            fresh.to_parquet(temp, index=False)
            temp.replace(target)
            written += len(fresh)
            files.append(str(target))

        return {
            "dataset": dataset,
            "received": received,
            "written": written,
            "duplicates": duplicate_count,
            "files": files,
        }

    def read(
        self,
        dataset: str,
        *,
        symbol: str | None = None,
        source: str | None = None,
        start=None,
        end=None,
        columns: Iterable[str] | None = None,
    ) -> pd.DataFrame:
        self._spec(dataset)
        base = self.root / dataset
        if not base.exists():
            return pd.DataFrame(columns=list(columns or []))

        pattern = "part-*.parquet"
        files = list(base.rglob(pattern))
        frames = []
        for file in files:
            if source and f"source={source}" not in file.parts:
                continue
            if symbol and f"symbol={symbol.upper()}" not in file.parts:
                continue
            frames.append(pd.read_parquet(file, columns=list(columns) if columns else None))
        if not frames:
            return pd.DataFrame(columns=list(columns or []))

        data = pd.concat(frames, ignore_index=True)
        if "timestamp" in data.columns:
            data["timestamp"] = self._normalize_timestamp(data["timestamp"])
            if start is not None:
                data = data[data["timestamp"] >= pd.Timestamp(start).tz_convert("UTC") if pd.Timestamp(start).tzinfo else data["timestamp"] >= pd.Timestamp(start, tz="UTC")]
            if end is not None:
                data = data[data["timestamp"] <= pd.Timestamp(end).tz_convert("UTC") if pd.Timestamp(end).tzinfo else data["timestamp"] <= pd.Timestamp(end, tz="UTC")]
        spec = self._spec(dataset)
        available_keys = [c for c in spec.dedup_columns if c in data.columns]
        if available_keys:
            data = data.drop_duplicates(available_keys, keep="first")
        return data.sort_values("timestamp").reset_index(drop=True) if "timestamp" in data.columns else data.reset_index(drop=True)

    def inventory(self) -> pd.DataFrame:
        rows = []
        for name, spec in DATASET_SPECS.items():
            data = self.read(name)
            rows.append({
                "dataset": name,
                "cadence": spec.cadence,
                "rows": len(data),
                "first_timestamp": data["timestamp"].min() if "timestamp" in data.columns and not data.empty else None,
                "last_timestamp": data["timestamp"].max() if "timestamp" in data.columns and not data.empty else None,
            })
        return pd.DataFrame(rows)
