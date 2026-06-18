from __future__ import annotations

import unittest

import pandas as pd

from notifier.watch_alert import maybe_send_watch_alert
from watchlist_scanner import analyze_candles


def _sample_crypto_frame() -> pd.DataFrame:
    rows = []
    index = pd.date_range("2026-06-18 00:00:00", periods=80, freq="15min", tz="UTC")
    for i in range(80):
        close = 100.0 + i * 0.08
        if i >= 76:
            close += (i - 75) * 0.55
        high = close + 0.12
        low = close - 0.35
        volume = 100.0
        if i == 79:
            volume = 420.0
        rows.append({
            "open": close - 0.08,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })
    return pd.DataFrame(rows, index=index)


class WatchlistScannerTests(unittest.TestCase):
    def test_crypto_structure_creates_b_grade_watch_event(self):
        result = analyze_candles("BTCUSDT", "crypto", _sample_crypto_frame())

        self.assertTrue(result["ok"])
        self.assertTrue(result["interesting"])
        event = result["event"]
        self.assertEqual(event["grade"], "B")
        self.assertTrue(event["not_entry"])
        self.assertTrue(event["confirmation_needed"])
        self.assertGreaterEqual(event["score"], event["min_score"])
        self.assertIn("NOT ENTRY", event["message_flags"])

    def test_stale_data_never_creates_live_watch_event(self):
        result = analyze_candles(
            "QQQ",
            "tradfi",
            _sample_crypto_frame(),
            stale=True,
            stale_reason="us_market_closed",
            timeframe="1h",
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result["interesting"])
        self.assertIsNone(result["event"])
        self.assertTrue(result["stale"])
        self.assertEqual(result["stale_reason"], "us_market_closed")

    def test_dry_run_watch_alert_does_not_send(self):
        result = analyze_candles("ETHUSDT", "crypto", _sample_crypto_frame())
        alert = maybe_send_watch_alert(result["event"], dry_run=True)

        self.assertFalse(alert["sent"])
        self.assertEqual(alert["reason"], "dry_run")
        self.assertTrue(alert["would_send"])
        self.assertIn("B-grade WATCH", alert["message"])
        self.assertIn("NOT ENTRY", alert["message"])
        self.assertIn("confirmation needed", alert["message"])


if __name__ == "__main__":
    unittest.main()
