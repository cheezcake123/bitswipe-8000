from __future__ import annotations

import unittest

from notifier.korean_alerts import display_alert_value


class KoreanAlertDisplayTests(unittest.TestCase):
    def test_known_internal_enum_gets_korean_display(self):
        self.assertEqual(
            display_alert_value("direction", "bullish_watch"),
            "상방 관찰",
        )

    def test_internal_enum_input_is_not_mutated(self):
        internal = "near_recent_support"
        displayed = display_alert_value("reason", internal)

        self.assertEqual(internal, "near_recent_support")
        self.assertEqual(displayed, "최근 지지선 근접")

    def test_unknown_value_is_preserved(self):
        self.assertEqual(
            display_alert_value("direction", "future_enum"),
            "future_enum",
        )

    def test_none_and_empty_use_fallback(self):
        self.assertEqual(
            display_alert_value("trend", None, fallback="확인 불가"),
            "확인 불가",
        )
        self.assertEqual(
            display_alert_value("trend", "  ", fallback="확인 불가"),
            "확인 불가",
        )

    def test_zero_value_is_not_hidden(self):
        self.assertEqual(display_alert_value("score", 0), "0")
        self.assertEqual(display_alert_value("score", 0.0), "0.0")


if __name__ == "__main__":
    unittest.main()
