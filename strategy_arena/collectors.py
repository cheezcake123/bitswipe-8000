from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd
import requests

from strategy_arena.market_store import AppendOnlyMarketStore

BINANCE_FUTURES_URL = "https://fapi.binance.com"
BINANCE_SPOT_URL = "https://api.binance.com"


@dataclass
class BinanceMarketCollector:
    store: AppendOnlyMarketStore
    session: requests.Session | None = None
    timeout: float = 10.0

    def __post_init__(self) -> None:
        if self.session is None:
            self.session = requests.Session()
            self.session.trust_env = False

    def _get(self, base: str, path: str, params: dict) -> object:
        assert self.session is not None
        response = self.session.get(f"{base}{path}", params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _minute_timestamp() -> pd.Timestamp:
        return pd.Timestamp(datetime.now(timezone.utc)).floor("min")

    @staticmethod
    def _five_minute_timestamp() -> pd.Timestamp:
        return pd.Timestamp(datetime.now(timezone.utc)).floor("5min")

    def collect_futures_ohlcv(self, symbol: str, limit: int = 3) -> dict:
        raw = self._get(BINANCE_FUTURES_URL, "/fapi/v1/klines", {"symbol": symbol, "interval": "1m", "limit": limit})
        columns = ["timestamp", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]
        frame = pd.DataFrame(raw, columns=columns)
        now_ms = int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)
        frame = frame[pd.to_numeric(frame["close_time"]) < now_ms]
        frame["timestamp"] = pd.to_datetime(frame["timestamp"].astype("int64"), unit="ms", utc=True)
        frame["close_time"] = pd.to_datetime(frame["close_time"].astype("int64"), unit="ms", utc=True)
        for col in ["open", "high", "low", "close", "volume"]:
            frame[col] = pd.to_numeric(frame[col], errors="raise")
        frame["symbol"] = symbol.upper()
        frame["source"] = "binance_usdm_rest"
        return self.store.append("futures_ohlcv", frame[["timestamp", "close_time", "symbol", "source", "open", "high", "low", "close", "volume"]])

    def collect_spot_ohlcv(self, symbol: str, limit: int = 3) -> dict:
        raw = self._get(BINANCE_SPOT_URL, "/api/v3/klines", {"symbol": symbol, "interval": "1m", "limit": limit})
        columns = ["timestamp", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]
        frame = pd.DataFrame(raw, columns=columns)
        now_ms = int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)
        frame = frame[pd.to_numeric(frame["close_time"]) < now_ms]
        frame["timestamp"] = pd.to_datetime(frame["timestamp"].astype("int64"), unit="ms", utc=True)
        frame["close_time"] = pd.to_datetime(frame["close_time"].astype("int64"), unit="ms", utc=True)
        for col in ["open", "high", "low", "close", "volume"]:
            frame[col] = pd.to_numeric(frame[col], errors="raise")
        frame["symbol"] = symbol.upper()
        frame["source"] = "binance_spot_rest"
        return self.store.append("spot_ohlcv", frame[["timestamp", "close_time", "symbol", "source", "open", "high", "low", "close", "volume"]])

    def collect_funding_events(self, symbol: str, lookback_hours: int = 24) -> dict:
        end = pd.Timestamp.now(tz="UTC")
        start = end - pd.Timedelta(hours=lookback_hours)
        raw = self._get(BINANCE_FUTURES_URL, "/fapi/v1/fundingRate", {
            "symbol": symbol,
            "startTime": int(start.timestamp() * 1000),
            "endTime": int(end.timestamp() * 1000),
            "limit": 1000,
        })
        frame = pd.DataFrame(raw)
        if frame.empty:
            return self.store.append("funding_rate", frame)
        frame["timestamp"] = pd.to_datetime(frame["fundingTime"].astype("int64"), unit="ms", utc=True)
        frame["funding_rate"] = pd.to_numeric(frame["fundingRate"], errors="raise")
        frame["mark_price"] = pd.to_numeric(frame.get("markPrice"), errors="coerce")
        frame["symbol"] = symbol.upper()
        frame["source"] = "binance_usdm_rest"
        return self.store.append("funding_rate", frame[["timestamp", "symbol", "source", "funding_rate", "mark_price"]])

    def collect_open_interest(self, symbol: str) -> dict:
        raw = self._get(BINANCE_FUTURES_URL, "/fapi/v1/openInterest", {"symbol": symbol})
        frame = pd.DataFrame([{
            "timestamp": self._five_minute_timestamp(),
            "symbol": symbol.upper(),
            "source": "binance_usdm_rest",
            "open_interest": float(raw["openInterest"]),
        }])
        return self.store.append("open_interest", frame)

    def collect_prices(self, symbol: str) -> dict[str, dict]:
        timestamp = self._minute_timestamp()
        premium = self._get(BINANCE_FUTURES_URL, "/fapi/v1/premiumIndex", {"symbol": symbol})
        perp = self._get(BINANCE_FUTURES_URL, "/fapi/v1/ticker/price", {"symbol": symbol})
        spot = self._get(BINANCE_SPOT_URL, "/api/v3/ticker/price", {"symbol": symbol})

        source_fut = "binance_usdm_rest"
        source_spot = "binance_spot_rest"
        common_fut = {"timestamp": timestamp, "symbol": symbol.upper(), "source": source_fut}
        common_spot = {"timestamp": timestamp, "symbol": symbol.upper(), "source": source_spot}
        return {
            "perpetual_price": self.store.append("perpetual_price", pd.DataFrame([{**common_fut, "price": float(perp["price"])}])),
            "spot_price": self.store.append("spot_price", pd.DataFrame([{**common_spot, "price": float(spot["price"])}])),
            "mark_price": self.store.append("mark_price", pd.DataFrame([{**common_fut, "price": float(premium["markPrice"])}])),
            "index_price": self.store.append("index_price", pd.DataFrame([{**common_fut, "price": float(premium["indexPrice"])}])),
        }

    def collect_once(self, symbol: str = "BTCUSDT") -> dict[str, object]:
        """Collect public market data only. This method never places or signs an order."""
        return {
            "futures_ohlcv": self.collect_futures_ohlcv(symbol),
            "spot_ohlcv": self.collect_spot_ohlcv(symbol),
            "funding_rate": self.collect_funding_events(symbol),
            "open_interest": self.collect_open_interest(symbol),
            "prices": self.collect_prices(symbol),
            "live_trading": False,
        }
