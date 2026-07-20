from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

BINANCE_FUTURES_URL = "https://fapi.binance.com"
HOUR_MS = 60 * 60 * 1000
DAY_MS = 24 * HOUR_MS


def _to_ms(value: datetime | pd.Timestamp | str | int) -> int:
    if isinstance(value, int):
        return value
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return int(ts.timestamp() * 1000)


def _utc_ms(values: Iterable[int]) -> pd.DatetimeIndex:
    return pd.to_datetime(list(values), unit="ms", utc=True)


@dataclass
class BinanceHistoricalClient:
    base_url: str = BINANCE_FUTURES_URL
    timeout: float = 15.0
    pause_seconds: float = 0.05
    session: requests.Session | None = None

    def __post_init__(self) -> None:
        if self.session is None:
            self.session = requests.Session()
            self.session.trust_env = False

    def _get(self, path: str, params: dict) -> list:
        assert self.session is not None
        response = self.session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError(f"Unexpected Binance response for {path}: {type(payload).__name__}")
        if self.pause_seconds:
            time.sleep(self.pause_seconds)
        return payload

    def fetch_ohlcv(self, symbol: str, interval: str, start, end) -> pd.DataFrame:
        start_ms, end_ms = _to_ms(start), _to_ms(end)
        cursor = start_ms
        rows: list[list] = []
        while cursor <= end_ms:
            page = self._get("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "startTime": cursor, "endTime": end_ms, "limit": 1500})
            if not page:
                break
            rows.extend(page)
            next_cursor = int(page[-1][0]) + 1
            if next_cursor <= cursor:
                raise RuntimeError("OHLCV pagination cursor did not advance")
            cursor = next_cursor
            if len(page) < 1500:
                break
        columns = ["timestamp", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]
        df = pd.DataFrame(rows, columns=columns)
        if df.empty:
            return df
        df["timestamp"] = _utc_ms(df["timestamp"].astype("int64"))
        df["close_time"] = _utc_ms(df["close_time"].astype("int64"))
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="raise")
        df["symbol"] = symbol
        df["source"] = "binance_usdm"
        return df[["timestamp", "close_time", "symbol", "source", "open", "high", "low", "close", "volume"]].drop_duplicates("timestamp")

    def fetch_funding_history(self, symbol: str, start, end) -> pd.DataFrame:
        start_ms, end_ms = _to_ms(start), _to_ms(end)
        cursor = start_ms
        rows: list[dict] = []
        while cursor <= end_ms:
            page = self._get("/fapi/v1/fundingRate", {"symbol": symbol, "startTime": cursor, "endTime": end_ms, "limit": 1000})
            if not page:
                break
            rows.extend(page)
            next_cursor = int(page[-1]["fundingTime"]) + 1
            if next_cursor <= cursor:
                raise RuntimeError("Funding pagination cursor did not advance")
            cursor = next_cursor
            if len(page) < 1000:
                break
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df["timestamp"] = pd.to_datetime(df["fundingTime"].astype("int64"), unit="ms", utc=True)
        df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="raise")
        df["mark_price"] = pd.to_numeric(df.get("markPrice"), errors="coerce")
        df["symbol"] = symbol
        df["source"] = "binance_usdm"
        return df[["timestamp", "symbol", "source", "funding_rate", "mark_price"]].drop_duplicates("timestamp")

    def fetch_open_interest_history(self, symbol: str, period: str, start, end) -> pd.DataFrame:
        start_ms, end_ms = _to_ms(start), _to_ms(end)
        if end_ms - start_ms > 31 * DAY_MS:
            raise ValueError("Binance openInterestHist only exposes the latest 1 month; request <= 31 days")
        cursor = start_ms
        rows: list[dict] = []
        while cursor <= end_ms:
            page = self._get("/futures/data/openInterestHist", {"symbol": symbol, "period": period, "startTime": cursor, "endTime": end_ms, "limit": 500})
            if not page:
                break
            rows.extend(page)
            next_cursor = int(page[-1]["timestamp"]) + 1
            if next_cursor <= cursor:
                raise RuntimeError("OI pagination cursor did not advance")
            cursor = next_cursor
            if len(page) < 500:
                break
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype("int64"), unit="ms", utc=True)
        df["open_interest"] = pd.to_numeric(df["sumOpenInterest"], errors="raise")
        df["open_interest_value"] = pd.to_numeric(df["sumOpenInterestValue"], errors="coerce")
        df["symbol"] = symbol
        df["source"] = "binance_usdm"
        return df[["timestamp", "symbol", "source", "open_interest", "open_interest_value"]].drop_duplicates("timestamp")


def save_parquet(df: pd.DataFrame, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(target, index=False)
    return target


def load_parquet(path: str | Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    for col in ["timestamp", "close_time", "decision_timestamp", "funding_available_at", "oi_available_at"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True)
    return df


class DataValidationError(RuntimeError):
    pass


@dataclass
class ValidationReport:
    warnings: list[str]
    rows: int


def _ensure_utc(series: pd.Series, name: str) -> None:
    if not pd.api.types.is_datetime64_any_dtype(series):
        raise DataValidationError(f"{name} must be datetime")
    if getattr(series.dt, "tz", None) is None:
        raise DataValidationError(f"{name} must be timezone-aware UTC")


def validate_source_table(df: pd.DataFrame, timestamp_col: str, name: str) -> None:
    if df.empty:
        raise DataValidationError(f"{name} is empty")
    _ensure_utc(df[timestamp_col], f"{name}.{timestamp_col}")
    if df[timestamp_col].duplicated().any():
        raise DataValidationError(f"{name} contains duplicate timestamps")
    if not df[timestamp_col].is_monotonic_increasing:
        raise DataValidationError(f"{name} timestamps are not sorted ascending")


def validate_research_dataset(df: pd.DataFrame, expected_interval: str = "1h") -> ValidationReport:
    warnings: list[str] = []
    required = ["timestamp", "close_time", "decision_timestamp", "open", "high", "low", "close", "volume", "funding_rate", "funding_available_at", "open_interest", "oi_available_at"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise DataValidationError(f"dataset missing columns: {missing}")
    validate_source_table(df, "timestamp", "research_dataset")
    for col in ["close_time", "decision_timestamp", "funding_available_at", "oi_available_at"]:
        _ensure_utc(df[col], col)
    expected = pd.Timedelta(expected_interval)
    gaps = df["timestamp"].diff().dropna()
    if (gaps != expected).any():
        sample = df.loc[gaps[gaps != expected].index[:5], "timestamp"].astype(str).tolist()
        raise DataValidationError(f"OHLCV missing/irregular candles near: {sample}")
    if df[["open", "high", "low", "close", "volume"]].isna().any().any():
        raise DataValidationError("OHLCV contains missing values")
    if df["funding_rate"].isna().any():
        warnings.append("Funding is missing for some early candles before the first available funding event")
    if df["open_interest"].isna().any():
        raise DataValidationError("OI missing after alignment; shorten the test period or inspect source data")
    funding_future = df["funding_available_at"].notna() & (df["funding_available_at"] > df["decision_timestamp"])
    oi_future = df["oi_available_at"].notna() & (df["oi_available_at"] > df["decision_timestamp"])
    if funding_future.any() or oi_future.any():
        raise DataValidationError("Look-ahead detected: funding/OI availability timestamp is after decision timestamp")
    return ValidationReport(warnings=warnings, rows=len(df))


def build_research_dataset(ohlcv: pd.DataFrame, funding: pd.DataFrame, oi: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Backward-only as-of joins. Decisions occur at candle close; execution occurs next open."""
    validate_source_table(ohlcv.sort_values("timestamp"), "timestamp", "ohlcv")
    validate_source_table(funding.sort_values("timestamp"), "timestamp", "funding")
    validate_source_table(oi.sort_values("timestamp"), "timestamp", "open_interest")
    base = ohlcv.copy().sort_values("timestamp").reset_index(drop=True)
    base["decision_timestamp"] = base["close_time"]
    f = funding[["timestamp", "funding_rate"]].copy().sort_values("timestamp").rename(columns={"timestamp": "funding_available_at"})
    base = pd.merge_asof(base.sort_values("decision_timestamp"), f, left_on="decision_timestamp", right_on="funding_available_at", direction="backward", allow_exact_matches=True)
    o = oi[["timestamp", "open_interest", "open_interest_value"]].copy().sort_values("timestamp").rename(columns={"timestamp": "oi_available_at"})
    base = pd.merge_asof(base.sort_values("decision_timestamp"), o, left_on="decision_timestamp", right_on="oi_available_at", direction="backward", allow_exact_matches=True)
    events = funding[["timestamp", "funding_rate"]].copy()
    payment_rates: list[float] = []
    for row in base[["timestamp", "close_time"]].itertuples(index=False):
        mask = (events["timestamp"] > row.timestamp) & (events["timestamp"] <= row.close_time)
        payment_rates.append(float(events.loc[mask, "funding_rate"].sum()) if mask.any() else 0.0)
    base["funding_payment_rate"] = payment_rates
    base["symbol"] = symbol
    return base.sort_values("timestamp").reset_index(drop=True)
