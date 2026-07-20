from __future__ import annotations

import io
import zipfile

import pandas as pd

import strategy_arena.basis_research as research

ESSENTIAL_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "close_time"]


def _fixed_read_kline_zip(content: bytes) -> pd.DataFrame:
    """Read both Spot and USD-M monthly kline archive header variants.

    Binance public archives have used different header names across products and periods.
    Basis research only needs the seven canonical OHLCV/time fields, so unrelated trade-count
    column names are intentionally ignored instead of making the parser fragile.
    """
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        csvs = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csvs:
            raise RuntimeError("No CSV found in Binance archive")
        with zf.open(csvs[0]) as fh:
            raw = pd.read_csv(fh)
        aliases = {
            "open_time": "timestamp",
            "Open time": "timestamp",
            "Open Time": "timestamp",
            "closeTime": "close_time",
            "Close time": "close_time",
            "Close Time": "close_time",
        }
        raw = raw.rename(columns={k: v for k, v in aliases.items() if k in raw.columns})
        if "timestamp" not in raw.columns or "close_time" not in raw.columns:
            with zf.open(csvs[0]) as fh:
                raw = pd.read_csv(fh, header=None, names=research.KLINE_COLUMNS)

    missing = [c for c in ESSENTIAL_COLUMNS if c not in raw.columns]
    if missing:
        raise RuntimeError(f"Unsupported Binance kline archive columns; missing={missing}, columns={list(raw.columns)}")

    unit = research._epoch_unit(raw["timestamp"])
    close_unit = research._epoch_unit(raw["close_time"])
    raw["timestamp"] = pd.to_datetime(pd.to_numeric(raw["timestamp"], errors="raise"), unit=unit, utc=True).astype("datetime64[ms, UTC]")
    raw["close_time"] = pd.to_datetime(pd.to_numeric(raw["close_time"], errors="raise"), unit=close_unit, utc=True).astype("datetime64[ms, UTC]")
    for col in ["open", "high", "low", "close", "volume"]:
        raw[col] = pd.to_numeric(raw[col], errors="raise")
    return raw[ESSENTIAL_COLUMNS].sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def main() -> None:
    research._read_kline_zip = _fixed_read_kline_zip
    research.main()


if __name__ == "__main__":
    main()
