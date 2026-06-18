from __future__ import annotations

import concurrent.futures
import math
import time
from datetime import datetime, time as dt_time, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pandas as pd

import config as runtime_config
from data_fetcher import fetch_current_price, fetch_ohlcv
from indicators import add_all_indicators


CRYPTO_INTERVAL = "15m"
CRYPTO_CANDLE_LIMIT = 120
TRADFI_INTERVAL = "1h"
TRADFI_PERIOD = "30d"
CRYPTO_STALE_MINUTES = 45
CRYPTO_MIN_SCORE = 70
TRADFI_MIN_SCORE = 75

_CACHE: dict[tuple[Any, ...], tuple[float, Any]] = {}


def _now_ts() -> float:
    return time.time()


def _cache_get(key: tuple[Any, ...], ttl: int):
    cached = _CACHE.get(key)
    if not cached:
        return None
    ts, value = cached
    if _now_ts() - ts > ttl:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: tuple[Any, ...], value: Any) -> Any:
    _CACHE[key] = (_now_ts(), value)
    if len(_CACHE) > 100:
        for old_key in sorted(_CACHE, key=lambda k: _CACHE[k][0])[:20]:
            _CACHE.pop(old_key, None)
    return value


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _pct(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous in (None, 0):
        return None
    return (current - previous) / previous * 100.0


def _round(value: Any, digits: int = 4):
    number = _safe_float(value)
    return None if number is None else round(number, digits)


def _timestamp_age_hours(index_value: Any) -> Optional[float]:
    try:
        ts = pd.Timestamp(index_value)
        if ts.tzinfo is None:
            ts = ts.tz_localize(timezone.utc)
        else:
            ts = ts.tz_convert(timezone.utc)
        return max(0.0, (pd.Timestamp.now(tz=timezone.utc) - ts).total_seconds() / 3600)
    except Exception:
        return None


def _timestamp_iso(index_value: Any) -> Optional[str]:
    try:
        ts = pd.Timestamp(index_value)
        if ts.tzinfo is None:
            ts = ts.tz_localize(timezone.utc)
        else:
            ts = ts.tz_convert(timezone.utc)
        return ts.isoformat()
    except Exception:
        return None


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    normalized = df.copy()
    normalized.columns = [str(col).lower() for col in normalized.columns]
    needed = ["open", "high", "low", "close", "volume"]
    for col in needed:
        if col not in normalized.columns:
            normalized[col] = 0.0
        normalized[col] = pd.to_numeric(normalized[col], errors="coerce")
    normalized = normalized.dropna(subset=["close"])
    return normalized[needed]


def _with_indicators(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    normalized = _normalize_ohlcv(df)
    if len(normalized) < 30:
        return normalized
    try:
        return add_all_indicators(normalized.copy(), tf=tf)
    except Exception:
        return normalized


def _trend_label(last: pd.Series) -> str:
    close = _safe_float(last.get("close"))
    ema9 = _safe_float(last.get("ema_9"))
    sma50 = _safe_float(last.get("sma_50"))
    macd_hist = _safe_float(last.get("macd_hist"))
    if close is None or ema9 is None or sma50 is None:
        return "unknown"
    bullish = close > sma50 and ema9 > sma50 and (macd_hist is None or macd_hist >= 0)
    bearish = close < sma50 and ema9 < sma50 and (macd_hist is None or macd_hist <= 0)
    if bullish:
        return "uptrend"
    if bearish:
        return "downtrend"
    return "mixed"


def _volume_ratio(df: pd.DataFrame, last: pd.Series) -> Optional[float]:
    volume = _safe_float(last.get("volume"))
    if volume is None:
        return None
    volume_ma = _safe_float(last.get("volume_ma"))
    if volume_ma is None or volume_ma <= 0:
        sample = pd.to_numeric(df["volume"].tail(21).head(20), errors="coerce").dropna()
        volume_ma = float(sample.mean()) if len(sample) else None
    if volume_ma is None or volume_ma <= 0:
        return None
    return volume / volume_ma


def _change_over_bars(df: pd.DataFrame, bars: int, price: float) -> Optional[float]:
    if len(df) <= bars:
        return None
    previous = _safe_float(df.iloc[-bars - 1].get("close"))
    return _pct(price, previous)


def _event_from_metrics(
    *,
    symbol: str,
    asset_class: str,
    price: float,
    support: float,
    resistance: float,
    distance_support_pct: float,
    distance_resistance_pct: float,
    change_1h_pct: Optional[float],
    change_4h_pct: Optional[float],
    rsi: Optional[float],
    trend: str,
    volume_ratio: Optional[float],
    stale: bool,
    stale_reason: Optional[str],
) -> tuple[Optional[dict], list[str], int]:
    if stale:
        return None, [stale_reason or "stale_data"], 0

    is_crypto = asset_class == "crypto"
    proximity_threshold = 0.45 if is_crypto else 1.10
    strong_1h_threshold = 0.75 if is_crypto else 1.40
    strong_4h_threshold = 1.50 if is_crypto else 2.80
    min_score = CRYPTO_MIN_SCORE if is_crypto else TRADFI_MIN_SCORE

    score = 0
    reasons: list[str] = []
    event_type = "structure_watch"
    direction = "two_way"
    confirmation = "Wait for candle close confirmation and a clean retest. NOT ENTRY."

    near_support = distance_support_pct <= proximity_threshold
    near_resistance = distance_resistance_pct <= proximity_threshold

    if near_support:
        score += 25
        reasons.append("near_recent_support")
        event_type = "near_support_watch"
        direction = "two_way"
        confirmation = "Watch for either support hold or breakdown. Confirmation needed."

    if near_resistance:
        score += 25
        reasons.append("near_recent_resistance")
        event_type = "near_resistance_watch"
        direction = "two_way"
        confirmation = "Watch for breakout close or rejection. Confirmation needed."

    if near_support and trend in ("downtrend", "mixed") and rsi is not None and 30 <= rsi <= 48:
        score += 25
        reasons.append("possible_reversal_near_support")
        event_type = "reversal_watch_near_support"
        direction = "bullish_watch"

    if near_resistance and trend in ("uptrend", "mixed") and rsi is not None and 52 <= rsi <= 72:
        score += 20
        reasons.append("possible_rejection_or_breakout_near_resistance")
        event_type = "resistance_decision_watch"
        direction = "two_way"

    abs_1h = abs(change_1h_pct) if change_1h_pct is not None else 0.0
    abs_4h = abs(change_4h_pct) if change_4h_pct is not None else 0.0
    if abs_1h >= strong_1h_threshold:
        score += 25
        reasons.append("strong_1h_move_pullback_needed")
        event_type = "strong_move_pullback_watch"
        direction = "bullish_watch" if (change_1h_pct or 0) > 0 else "bearish_watch"
        confirmation = "Strong move detected; wait for pullback/retest. NOT ENTRY."

    if abs_4h >= strong_4h_threshold:
        score += 15
        reasons.append("strong_4h_move")

    if volume_ratio is not None:
        if volume_ratio >= 2.0:
            score += 25
            reasons.append("volume_spike")
        elif volume_ratio >= 1.5:
            score += 15
            reasons.append("volume_expansion")

    if trend in ("uptrend", "downtrend"):
        score += 10
        reasons.append(f"{trend}_context")

    if score < min_score or len(reasons) < 2:
        return None, reasons, score

    event = {
        "grade": "B",
        "label": "B-grade WATCH",
        "not_entry": True,
        "confirmation_needed": True,
        "event_type": event_type,
        "direction": direction,
        "asset_class": asset_class,
        "symbol": symbol,
        "score": score,
        "min_score": min_score,
        "price": round(price, 4),
        "support": round(support, 4),
        "resistance": round(resistance, 4),
        "distance_support_pct": round(distance_support_pct, 4),
        "distance_resistance_pct": round(distance_resistance_pct, 4),
        "change_1h_pct": _round(change_1h_pct),
        "change_4h_pct": _round(change_4h_pct),
        "rsi": _round(rsi, 2),
        "trend": trend,
        "volume_ratio": _round(volume_ratio, 3),
        "reasons": reasons[:6],
        "confirmation": confirmation,
        "message_flags": ["NOT ENTRY", "confirmation needed"],
        "stale": False,
    }
    return event, reasons, score


def analyze_candles(
    symbol: str,
    asset_class: str,
    df: pd.DataFrame,
    *,
    price_override: Optional[float] = None,
    stale: bool = False,
    stale_reason: Optional[str] = None,
    timeframe: str = CRYPTO_INTERVAL,
) -> dict:
    df = _with_indicators(df, tf=timeframe)
    symbol = symbol.upper()
    if df.empty or len(df) < 30:
        return {
            "ok": False,
            "symbol": symbol,
            "asset_class": asset_class,
            "interesting": False,
            "event": None,
            "reason": "not_enough_candles",
        }

    last = df.iloc[-1]
    price = _safe_float(price_override) or _safe_float(last.get("close"))
    if price is None or price <= 0:
        return {
            "ok": False,
            "symbol": symbol,
            "asset_class": asset_class,
            "interesting": False,
            "event": None,
            "reason": "missing_price",
        }

    lookback = df.tail(min(len(df), 96 if asset_class == "crypto" else 60))
    support = _safe_float(lookback["low"].min()) or price
    resistance = _safe_float(lookback["high"].max()) or price
    distance_support_pct = abs(price - support) / price * 100.0 if price else 999.0
    distance_resistance_pct = abs(resistance - price) / price * 100.0 if price else 999.0
    rsi = _safe_float(last.get("rsi"))
    trend = _trend_label(last)
    vol_ratio = _volume_ratio(df, last)
    change_1h_pct = _change_over_bars(df, 4 if asset_class == "crypto" else 1, price)
    change_4h_pct = _change_over_bars(df, 16 if asset_class == "crypto" else 4, price)

    event, reasons, score = _event_from_metrics(
        symbol=symbol,
        asset_class=asset_class,
        price=price,
        support=support,
        resistance=resistance,
        distance_support_pct=distance_support_pct,
        distance_resistance_pct=distance_resistance_pct,
        change_1h_pct=change_1h_pct,
        change_4h_pct=change_4h_pct,
        rsi=rsi,
        trend=trend,
        volume_ratio=vol_ratio,
        stale=stale,
        stale_reason=stale_reason,
    )

    return {
        "ok": True,
        "symbol": symbol,
        "asset_class": asset_class,
        "timeframe": timeframe,
        "interesting": event is not None,
        "event": event,
        "score": score,
        "reasons": reasons,
        "price": round(price, 4),
        "support": round(support, 4),
        "resistance": round(resistance, 4),
        "distance_support_pct": round(distance_support_pct, 4),
        "distance_resistance_pct": round(distance_resistance_pct, 4),
        "change_1h_pct": _round(change_1h_pct),
        "change_4h_pct": _round(change_4h_pct),
        "rsi": _round(rsi, 2),
        "trend": trend,
        "volume_ratio": _round(vol_ratio, 3),
        "last_candle_at": _timestamp_iso(df.index[-1]),
        "stale": stale,
        "stale_reason": stale_reason,
    }


def _fetch_crypto_ohlcv(symbol: str) -> pd.DataFrame:
    key = ("crypto_ohlcv", symbol, CRYPTO_INTERVAL, CRYPTO_CANDLE_LIMIT)
    cached = _cache_get(key, runtime_config.WATCH_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached.copy()
    df = fetch_ohlcv(symbol, CRYPTO_INTERVAL, limit=CRYPTO_CANDLE_LIMIT)
    return _cache_set(key, df.copy()).copy()


def _fetch_crypto_price(symbol: str) -> Optional[float]:
    key = ("crypto_price", symbol)
    cached = _cache_get(key, min(60, runtime_config.WATCH_CACHE_TTL_SECONDS))
    if cached is not None:
        return cached
    try:
        return _cache_set(key, fetch_current_price(symbol))
    except Exception:
        return None


def scan_crypto_symbol(symbol: str) -> dict:
    symbol = symbol.upper()
    try:
        df = _fetch_crypto_ohlcv(symbol)
        price = _fetch_crypto_price(symbol)
        age = _timestamp_age_hours(df.index[-1]) if not df.empty else None
        stale = age is None or age > (CRYPTO_STALE_MINUTES / 60.0)
        stale_reason = "stale_crypto_data" if stale else None
        result = analyze_candles(
            symbol,
            "crypto",
            df,
            price_override=price,
            stale=stale,
            stale_reason=stale_reason,
            timeframe=CRYPTO_INTERVAL,
        )
        result["data_age_hours"] = _round(age, 3)
        return result
    except Exception as exc:
        return {
            "ok": False,
            "symbol": symbol,
            "asset_class": "crypto",
            "interesting": False,
            "event": None,
            "reason": "crypto_scan_error",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _us_market_open_now(now: Optional[datetime] = None) -> bool:
    try:
        et = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/New_York"))
    except Exception:
        et = datetime.utcnow().replace(tzinfo=timezone.utc)
    if et.weekday() >= 5:
        return False
    market_open = dt_time(9, 30)
    market_close = dt_time(16, 0)
    return market_open <= et.time() <= market_close


def _extract_yfinance_frame(data: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if data is None or data.empty:
        return pd.DataFrame()
    if isinstance(data.columns, pd.MultiIndex):
        if symbol in data.columns.get_level_values(0):
            frame = data[symbol].copy()
        elif symbol in data.columns.get_level_values(1):
            frame = data.xs(symbol, level=1, axis=1).copy()
        else:
            return pd.DataFrame()
    else:
        frame = data.copy()
    rename = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    }
    frame = frame.rename(columns=rename)
    return _normalize_ohlcv(frame)


def _download_tradfi(symbols: list[str]) -> pd.DataFrame:
    cache_key = ("tradfi_batch", tuple(symbols), TRADFI_PERIOD, TRADFI_INTERVAL)
    cached = _cache_get(cache_key, runtime_config.WATCH_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached.copy()
    import yfinance as yf

    data = yf.download(
        symbols,
        period=TRADFI_PERIOD,
        interval=TRADFI_INTERVAL,
        group_by="ticker",
        progress=False,
        auto_adjust=True,
        threads=False,
        timeout=runtime_config.YFINANCE_TIMEOUT_SECS,
    )
    return _cache_set(cache_key, data.copy()).copy()


def scan_tradfi_symbols(symbols: list[str]) -> list[dict]:
    symbols = [symbol.upper() for symbol in symbols if symbol]
    if not symbols:
        return []

    market_open = _us_market_open_now()
    try:
        data = _download_tradfi(symbols)
    except ImportError:
        return [
            {
                "ok": False,
                "symbol": symbol,
                "asset_class": "tradfi",
                "interesting": False,
                "event": None,
                "reason": "yfinance_not_installed",
            }
            for symbol in symbols
        ]
    except Exception as exc:
        return [
            {
                "ok": False,
                "symbol": symbol,
                "asset_class": "tradfi",
                "interesting": False,
                "event": None,
                "reason": "tradfi_scan_error",
                "error": f"{type(exc).__name__}: {exc}",
            }
            for symbol in symbols
        ]

    results = []
    for symbol in symbols:
        df = _extract_yfinance_frame(data, symbol)
        age = _timestamp_age_hours(df.index[-1]) if not df.empty else None
        stale = (
            not market_open
            or age is None
            or age > runtime_config.WATCH_TRADFI_STALE_HOURS
        )
        if not market_open:
            stale_reason = "us_market_closed"
        elif age is None or age > runtime_config.WATCH_TRADFI_STALE_HOURS:
            stale_reason = "stale_tradfi_data"
        else:
            stale_reason = None
        result = analyze_candles(
            symbol,
            "tradfi",
            df,
            stale=stale,
            stale_reason=stale_reason,
            timeframe=TRADFI_INTERVAL,
        )
        result["market_open"] = market_open
        result["data_age_hours"] = _round(age, 3)
        results.append(result)
    return results


def scan_watchlist(
    crypto_symbols: Optional[list[str]] = None,
    tradfi_symbols: Optional[list[str]] = None,
) -> dict:
    crypto_symbols = crypto_symbols if crypto_symbols is not None else runtime_config.WATCH_CRYPTO_SYMBOLS
    tradfi_symbols = tradfi_symbols if tradfi_symbols is not None else runtime_config.WATCH_TRADFI_SYMBOLS

    crypto_symbols = [symbol.upper() for symbol in crypto_symbols if symbol]
    tradfi_symbols = [symbol.upper() for symbol in tradfi_symbols if symbol]

    started = datetime.now(timezone.utc).isoformat()
    crypto_results: list[dict] = []
    workers = min(runtime_config.WATCH_MAX_WORKERS, max(1, len(crypto_symbols)))
    if crypto_symbols:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(scan_crypto_symbol, symbol) for symbol in crypto_symbols]
            for future in concurrent.futures.as_completed(futures):
                crypto_results.append(future.result())
        crypto_results.sort(key=lambda row: crypto_symbols.index(row.get("symbol")) if row.get("symbol") in crypto_symbols else 999)

    tradfi_results = scan_tradfi_symbols(tradfi_symbols)
    results = crypto_results + tradfi_results
    events = [row["event"] for row in results if row.get("event")]
    return {
        "ok": True,
        "started_at": started,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "watch_enabled": runtime_config.WATCH_ENABLED,
        "crypto_symbols": crypto_symbols,
        "tradfi_symbols": tradfi_symbols,
        "scan_interval_seconds": runtime_config.WATCH_SCAN_INTERVAL_SECONDS,
        "results": results,
        "events": events,
        "event_count": len(events),
        "llm_calls": 0,
    }
