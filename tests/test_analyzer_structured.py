import unittest

import pandas as pd

from analyzer import (
    _confidence_from_breakdown,
    _extract_analysis_json,
    _levels_from_structured,
    _normalize_analysis_json,
    _render_report_from_structured,
    _signal_from_structured,
    _strip_analysis_json_block,
    parse_leverage,
    parse_report_sections,
    parse_signal,
    parse_trade_levels,
)


BULL_VIEW = "\uc0c1\ubc29 \uc6b0\uc704"
BEAR_VIEW = "\ud558\ubc29 \uc6b0\uc704"
NEUTRAL_VIEW = "\uc911\ub9bd"
HOLD_SIGNAL = "\ud640\ub4dc"


def _risk_payload(view, entry, stop, target, confidence=85):
    return {
        "view": view,
        "confidence": confidence,
        "confidence_breakdown": {
            "price_structure": 25,
            "momentum": 18,
            "derivatives": 17,
            "macro": 12,
            "account_risk_fit": 13,
            "data_quality_penalty": 0,
            "counter_scenario_penalty": 0,
        },
        "trade": {
            "entry": entry,
            "stop": stop,
            "target": target,
            "leverage": 3,
        },
    }


def _tf_row(direction):
    if direction == "bearish":
        return pd.DataFrame([
            {
                "close": 100.0,
                "sma_50": 105.0,
                "sma_200": 110.0,
                "macd_hist": -1.0,
                "rsi": 40.0,
                "supertrend_dir": -1.0,
            }
        ])
    return pd.DataFrame([
        {
            "close": 100.0,
            "sma_50": 95.0,
            "sma_200": 90.0,
            "macd_hist": 1.0,
            "rsi": 60.0,
            "supertrend_dir": 1.0,
        }
    ])


class AnalyzerStructuredOutputTests(unittest.TestCase):
    def test_extracts_json_and_strips_machine_block(self):
        raw = (
            '<analysis_json>{"view":"상방 우위","confidence":74,'
            '"trade":{"entry":"$100,000","stop":99000,"target":102000,"leverage":3},'
            '"levels":{"resistance":103000,"support":98500,'
            '"bull_trigger":101000,"bear_trigger":98000}}</analysis_json>\n'
            "📊 관점: 상방 우위\n"
            "💯 확신도: 74%"
        )

        parsed = _extract_analysis_json(raw)
        report = _strip_analysis_json_block(raw)

        self.assertEqual(parsed["confidence"], 74)
        self.assertEqual(_signal_from_structured(parsed), "매수")
        self.assertTrue(report.startswith("📊 관점"))
        self.assertNotIn("analysis_json", report)

    def test_structured_levels_normalize_prices(self):
        parsed = {
            "levels": {
                "resistance": "$103,500",
                "support": "N/A",
                "bull_trigger": 101000,
                "bear_trigger": "98,750.5",
            },
            "trade": {
                "entry": "$100,000",
                "stop": 99000,
                "target": None,
            },
        }

        levels = _levels_from_structured(parsed)

        self.assertEqual(levels["resistance"], 103500.0)
        self.assertIsNone(levels["support"])
        self.assertEqual(levels["bear_trigger"], 98750.5)
        self.assertEqual(levels["entry"], 100000.0)

    def test_renders_report_when_tool_use_omits_body(self):
        parsed = {
            "view": "하방 우위",
            "confidence": 55,
            "regime": "박스",
            "confidence_breakdown": {
                "price_structure": 16,
                "momentum": 11,
                "derivatives": 8,
                "macro": 7,
                "account_risk_fit": 2,
                "data_quality_penalty": -3,
                "counter_scenario_penalty": -6,
            },
            "data_quality_notes": ["마지막 캔들은 미완성봉"],
            "key_facts": ["1h 가격이 단기 지지에 근접"],
            "inferences": ["반등 실패 시 하방 압력이 우세"],
            "counter_scenario": ["78,550 회복 시 하방 관점 약화"],
            "levels": {
                "resistance": 78347.99,
                "support": 77207,
                "bull_trigger": 78550,
                "bear_trigger": 77207,
            },
            "trade": {
                "entry": None,
                "stop": 78550,
                "target": 76505,
                "leverage": 3,
            },
            "actions": {
                "aggressive": "77,207 이탈 확인 시 소액 숏",
                "conservative": "이탈 후 되돌림 실패까지 대기",
            },
            "invalidation": "78,550 회복",
            "summary": "박스 하단 이탈 여부가 핵심입니다.",
        }

        report = _render_report_from_structured(parsed)
        meta = parse_report_sections(report)
        signal, confidence = parse_signal(report)
        levels = parse_trade_levels(report)

        self.assertTrue(meta["format_ok"])
        self.assertEqual(signal, "매도")
        self.assertEqual(confidence, 35)
        self.assertEqual(parse_leverage(report), 3)
        self.assertEqual(levels["stop"], 78550.0)
        self.assertEqual(levels["target"], 76505.0)
        self.assertNotIn("record_analysis", report)

    def test_normalizes_confidence_to_breakdown_sum(self):
        parsed = {
            "confidence": 55,
            "confidence_breakdown": {
                "price_structure": 16,
                "momentum": 11,
                "derivatives": 8,
                "macro": 7,
                "account_risk_fit": 2,
                "data_quality_penalty": -3,
                "counter_scenario_penalty": -6,
            },
        }

        normalized, adjustments = _normalize_analysis_json(parsed)

        self.assertEqual(normalized["confidence"], 35)
        self.assertTrue(adjustments)

    def test_confidence_breakdown_clamps_to_one_to_one_hundred(self):
        low = {
            "confidence_breakdown": {
                "price_structure": 0,
                "momentum": 0,
                "derivatives": 0,
                "macro": 0,
                "account_risk_fit": 0,
                "data_quality_penalty": -15,
                "counter_scenario_penalty": -10,
            },
        }
        high = {
            "confidence_breakdown": {
                "price_structure": 30,
                "momentum": 20,
                "derivatives": 20,
                "macro": 15,
                "account_risk_fit": 15,
                "data_quality_penalty": 0,
                "counter_scenario_penalty": 0,
            },
        }

        self.assertEqual(_confidence_from_breakdown(low), 1)
        self.assertEqual(_confidence_from_breakdown(high), 100)

    def test_normalizes_zero_confidence_to_minimum_one(self):
        parsed = {
            "confidence": 0,
            "confidence_breakdown": {
                "price_structure": 0,
                "momentum": 0,
                "derivatives": 0,
                "macro": 0,
                "account_risk_fit": 0,
                "data_quality_penalty": 0,
                "counter_scenario_penalty": 0,
            },
        }

        normalized, adjustments = _normalize_analysis_json(parsed)

        self.assertEqual(normalized["confidence"], 1)
        self.assertTrue(adjustments)

    def test_invalid_long_stop_above_entry_is_rejected(self):
        parsed = _risk_payload(BULL_VIEW, entry=100, stop=101, target=105)

        normalized, adjustments = _normalize_analysis_json(parsed)

        self.assertEqual(normalized["view"], NEUTRAL_VIEW)
        self.assertEqual(_signal_from_structured(normalized), HOLD_SIGNAL)
        self.assertLessEqual(normalized["confidence"], 25)
        self.assertEqual(normalized["trade"]["risk_verdict"], "INVALID")
        self.assertFalse(normalized["trade"]["worth_taking"])
        self.assertIsNone(normalized["trade"]["entry"])
        self.assertTrue(any("Invalid LONG" in item for item in normalized["risk_warnings"]))
        self.assertTrue(any("risk_validation:" in item for item in adjustments))

    def test_invalid_short_stop_below_entry_is_rejected(self):
        parsed = _risk_payload(BEAR_VIEW, entry=100, stop=99, target=95)

        normalized, _ = _normalize_analysis_json(parsed)

        self.assertEqual(normalized["view"], NEUTRAL_VIEW)
        self.assertLessEqual(normalized["confidence"], 25)
        self.assertEqual(normalized["trade"]["risk_verdict"], "INVALID")
        self.assertFalse(normalized["trade"]["worth_taking"])
        self.assertIsNone(normalized["trade"]["stop"])
        self.assertTrue(any("Invalid SHORT" in item for item in normalized["risk_warnings"]))

    def test_poor_risk_reward_forces_no_trade(self):
        parsed = _risk_payload(BULL_VIEW, entry=100, stop=98, target=101)

        normalized, _ = _normalize_analysis_json(parsed)

        self.assertEqual(normalized["view"], NEUTRAL_VIEW)
        self.assertLessEqual(normalized["confidence"], 45)
        self.assertAlmostEqual(normalized["trade"]["risk_reward_ratio"], 0.5)
        self.assertFalse(normalized["trade"]["worth_taking"])
        self.assertTrue(any("Poor risk/reward" in item for item in normalized["risk_warnings"]))

    def test_conflicting_main_timeframes_reduce_confidence(self):
        parsed = _risk_payload(BULL_VIEW, entry=100, stop=98, target=104)
        multi_tf_data = {
            "15m": _tf_row("bullish"),
            "1h": _tf_row("bullish"),
            "4h": _tf_row("bearish"),
            "1d": _tf_row("bearish"),
        }

        normalized, adjustments = _normalize_analysis_json(parsed, multi_tf_data=multi_tf_data)

        self.assertEqual(normalized["view"], NEUTRAL_VIEW)
        self.assertLessEqual(normalized["confidence"], 45)
        self.assertEqual(normalized["trade"]["risk_verdict"], "TIMEFRAME_CONFLICT")
        self.assertFalse(normalized["trade"]["worth_taking"])
        self.assertEqual(normalized["timeframe_structure"]["4h"]["direction"], "bearish")
        self.assertTrue(any("timeframe_structure:" in item for item in adjustments))

    def test_normalizes_breakdown_components_before_summing(self):
        parsed = {
            "confidence": 55,
            "confidence_breakdown": {
                "price_structure": 999,
                "momentum": 20,
                "derivatives": 20,
                "macro": 15,
                "account_risk_fit": 15,
                "data_quality_penalty": 5,
                "counter_scenario_penalty": -99,
            },
        }

        normalized, adjustments = _normalize_analysis_json(parsed)

        self.assertEqual(normalized["confidence_breakdown"]["price_structure"], 30)
        self.assertEqual(normalized["confidence_breakdown"]["data_quality_penalty"], 0)
        self.assertEqual(normalized["confidence_breakdown"]["counter_scenario_penalty"], -10)
        self.assertEqual(normalized["confidence"], 60)
        self.assertGreaterEqual(len(adjustments), 4)

    def test_parse_signal_tolerates_markdown_wrapped_fields(self):
        report = (
            "📊 **관점:** **상방 우위**\n"
            "💯 **확신도:** **74%**\n"
        )

        signal, confidence = parse_signal(report)

        self.assertEqual(signal, "매수")
        self.assertEqual(confidence, 74)

    def test_parse_signal_clamps_confidence_to_one_to_one_hundred(self):
        self.assertEqual(parse_signal("💯 확신도: 0%")[1], 1)
        self.assertEqual(parse_signal("💯 확신도: 150%")[1], 100)


if __name__ == "__main__":
    unittest.main()
