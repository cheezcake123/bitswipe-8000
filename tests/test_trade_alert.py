from __future__ import annotations

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import notifier.telegram_notifier as telegram_notifier
import notifier.trade_alert as trade_alert
from scripts.test_detailed_trade_alert import build_test_payload


class TradeAlertPureFunctionTests(unittest.TestCase):
    def test_long_message_generation(self):
        payload = build_test_payload("LONG")
        message = trade_alert.build_trade_alert_message(payload, test_mode=True)

        self.assertIn("[테스트]", message)
        self.assertIn("[BitSwipe 진입 심사 보고서]", message)
        self.assertIn("롱 (LONG)", message)
        self.assertIn("1차 목표가 (1R): $102.00", message)
        self.assertIn("예상 손익비: 1:2.00", message)

    def test_short_message_generation(self):
        payload = build_test_payload("SHORT")
        message = trade_alert.build_trade_alert_message(payload, test_mode=True)

        self.assertIn("숏 (SHORT)", message)
        self.assertIn("1차 목표가 (1R): $98.0000", message)
        self.assertIn("예상 손익비: 1:2.00", message)

    def test_long_stop_direction_warning(self):
        payload = build_test_payload("LONG")
        payload["risk_guard"]["stop_price"] = 102.0

        candidate = trade_alert.normalize_trade_alert_payload(payload)
        message = trade_alert.build_trade_alert_message(payload)

        self.assertFalse(candidate["stop_direction_valid"])
        self.assertIn("LONG 손절가는 진입가보다 낮아야 합니다", message)

    def test_short_stop_direction_warning(self):
        payload = build_test_payload("SHORT")
        payload["risk_guard"]["stop_price"] = 98.0

        candidate = trade_alert.normalize_trade_alert_payload(payload)
        message = trade_alert.build_trade_alert_message(payload)

        self.assertFalse(candidate["stop_direction_valid"])
        self.assertIn("SHORT 손절가는 진입가보다 높아야 합니다", message)

    def test_long_one_r_target(self):
        self.assertEqual(
            trade_alert.calculate_one_r_target("LONG", 100.0, 98.0),
            102.0,
        )

    def test_short_one_r_target(self):
        self.assertEqual(
            trade_alert.calculate_one_r_target("SHORT", 100.0, 102.0),
            98.0,
        )

    def test_short_one_r_target_must_remain_positive(self):
        self.assertIsNone(
            trade_alert.calculate_one_r_target("SHORT", 1.0, 3.0)
        )

    def test_missing_optional_fields_are_safe(self):
        payload = {
            "symbol": "SAFEUSDT",
            "signal": "BUY",
            "confidence": 82,
            "risk_guard": {
                "verdict": "PASS",
                "side": "long",
                "entry_price": 100,
                "stop_price": 98,
                "target_price": 104,
            },
        }

        evaluation = trade_alert.evaluate_trade_alert(payload)
        message = trade_alert.build_trade_alert_message(payload)

        self.assertTrue(evaluation["eligible"])
        self.assertIn("정보 없음", message)
        self.assertLessEqual(len(message), trade_alert.TELEGRAM_MAX_MESSAGE_LENGTH)

    def test_invalid_numeric_types_are_safe(self):
        payload = build_test_payload("LONG")
        payload["confidence"] = "NaN"
        for key in (
            "entry_price",
            "stop_price",
            "target_price",
            "risk_reward_ratio",
            "leverage",
            "stop_distance_percent",
        ):
            payload["risk_guard"][key] = "not-a-number"
        payload["trade_levels"] = {}
        payload["analysis_json"]["trade"] = {}

        evaluation = trade_alert.evaluate_trade_alert(payload)
        message = trade_alert.build_trade_alert_message(payload)

        self.assertFalse(evaluation["eligible"])
        self.assertEqual(evaluation["reason"], "confidence_too_low")
        self.assertIn("진입가: N/A", message)

    def test_out_of_range_confidence_is_blocked(self):
        payload = build_test_payload("LONG")
        payload["confidence"] = 101

        evaluation = trade_alert.evaluate_trade_alert(payload)

        self.assertFalse(evaluation["eligible"])
        self.assertEqual(evaluation["reason"], "confidence_too_low")

    def test_empty_payload(self):
        evaluation = trade_alert.evaluate_trade_alert({})

        self.assertFalse(evaluation["eligible"])
        self.assertEqual(evaluation["reason"], "no_payload")

    def test_risk_guard_fail(self):
        payload = build_test_payload("LONG")
        payload["risk_guard"]["verdict"] = "FAIL"

        evaluation = trade_alert.evaluate_trade_alert(payload)

        self.assertFalse(evaluation["eligible"])
        self.assertEqual(evaluation["reason"], "risk_guard_not_pass")

    def test_confidence_below_75(self):
        payload = build_test_payload("LONG")
        payload["confidence"] = 74.999

        evaluation = trade_alert.evaluate_trade_alert(payload)

        self.assertFalse(evaluation["eligible"])
        self.assertEqual(evaluation["reason"], "confidence_too_low")

    def test_confidence_exactly_75_is_eligible(self):
        payload = build_test_payload("LONG")
        payload["confidence"] = 75

        evaluation = trade_alert.evaluate_trade_alert(payload)

        self.assertTrue(evaluation["eligible"])

    def test_non_long_short_signal(self):
        payload = build_test_payload("LONG")
        payload["signal"] = "HOLD"

        evaluation = trade_alert.evaluate_trade_alert(payload)

        self.assertFalse(evaluation["eligible"])
        self.assertEqual(evaluation["reason"], "signal_not_buy_or_sell")

    def test_guard_side_mismatch_is_blocked(self):
        payload = build_test_payload("LONG")
        payload["risk_guard"]["side"] = "short"

        evaluation = trade_alert.evaluate_trade_alert(payload)
        message = trade_alert.build_trade_alert_message(payload)

        self.assertFalse(evaluation["eligible"])
        self.assertEqual(evaluation["reason"], "risk_guard_side_mismatch")
        self.assertIn("방향 불일치 경고", message)

    def test_missing_current_price_does_not_fabricate_market_data(self):
        payload = build_test_payload("LONG")
        payload.pop("price")

        message = trade_alert.build_trade_alert_message(payload)

        self.assertIn("현재가: N/A", message)
        self.assertNotIn("현재가: $100.00", message)

    def test_long_message_respects_telegram_limit(self):
        payload = build_test_payload("LONG")
        very_long = "긴 해설 " * 3000
        payload["analysis_json"]["summary"] = very_long
        payload["analysis_json"]["key_facts"] = [very_long] * 20
        payload["report_sections"]["facts"] = [very_long] * 20
        payload["report_sections"]["interpretation"] = [very_long] * 20
        payload["report_sections"]["counter_scenario"] = [very_long] * 20

        message = trade_alert.build_trade_alert_message(payload)

        self.assertLessEqual(len(message), trade_alert.TELEGRAM_MAX_MESSAGE_LENGTH)
        self.assertIn("가격 계획", message)
        self.assertIn("자동 주문 신호가 아닙니다", message)

    def test_internal_enums_remain_english(self):
        payload = build_test_payload("LONG")
        payload["signal"] = "매수"

        evaluation = trade_alert.evaluate_trade_alert(payload)

        self.assertEqual(evaluation["side"], "LONG")
        self.assertEqual(evaluation["risk_guard_verdict"], "PASS")

    def test_message_is_deterministic(self):
        payload = build_test_payload("SHORT")

        first = trade_alert.build_trade_alert_message(payload, test_mode=True)
        second = trade_alert.build_trade_alert_message(
            copy.deepcopy(payload), test_mode=True
        )

        self.assertEqual(first, second)


class TradeAlertIsolationTests(unittest.TestCase):
    def test_dry_run_has_no_sender_state_or_log_io(self):
        payload = build_test_payload("LONG")
        with mock.patch.object(trade_alert, "send_telegram_message") as sender, \
             mock.patch.object(trade_alert, "_load_state") as load_state, \
             mock.patch.object(trade_alert, "_save_state") as save_state, \
             mock.patch.object(trade_alert, "_append_log") as append_log:
            result = trade_alert.maybe_send_trade_alert(
                payload,
                dry_run=True,
                test_mode=True,
            )

        self.assertEqual(result["reason"], "dry_run")
        self.assertFalse(result["sent"])
        sender.assert_not_called()
        load_state.assert_not_called()
        save_state.assert_not_called()
        append_log.assert_not_called()

    def test_dry_run_leaves_production_files_unchanged(self):
        payload = build_test_payload("SHORT")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            log_path = root / "log.jsonl"
            state_path.write_text('{"existing": 1}', encoding="utf-8")
            log_path.write_text('{"existing": true}\n', encoding="utf-8")
            before = (state_path.read_bytes(), log_path.read_bytes())

            with mock.patch.object(trade_alert, "STATE_PATH", state_path), \
                 mock.patch.object(trade_alert, "LOG_PATH", log_path):
                trade_alert.maybe_send_trade_alert(
                    payload,
                    dry_run=True,
                    test_mode=True,
                )

            after = (state_path.read_bytes(), log_path.read_bytes())
            self.assertEqual(before, after)

    def test_test_send_isolated_from_production_state(self):
        payload = build_test_payload("LONG")
        sender = mock.Mock(return_value={"ok": True})
        with mock.patch.object(trade_alert, "_load_state") as load_state, \
             mock.patch.object(trade_alert, "_save_state") as save_state, \
             mock.patch.object(trade_alert, "_append_log") as append_log:
            result = trade_alert.maybe_send_trade_alert(
                payload,
                test_mode=True,
                sender=sender,
            )

        self.assertTrue(result["sent"])
        self.assertEqual(result["reason"], "test_sent")
        sender.assert_called_once()
        load_state.assert_not_called()
        save_state.assert_not_called()
        append_log.assert_not_called()

    def test_test_mode_requires_explicit_sender(self):
        payload = build_test_payload("LONG")
        with mock.patch.object(trade_alert, "send_telegram_message") as default_sender:
            result = trade_alert.maybe_send_trade_alert(
                payload,
                test_mode=True,
            )

        self.assertFalse(result["sent"])
        self.assertEqual(result["reason"], "test_sender_required")
        default_sender.assert_not_called()

    def test_duplicate_cooldown_prevents_sender(self):
        payload = build_test_payload("LONG")
        sender = mock.Mock(return_value={"ok": True})
        with mock.patch.object(
            trade_alert,
            "_should_suppress_duplicate",
            return_value={
                "suppress": True,
                "reason": "duplicate_suppressed",
                "elapsed_seconds": 10,
                "cooldown_seconds": 3600,
            },
        ), mock.patch.object(trade_alert, "_append_log"):
            result = trade_alert.maybe_send_trade_alert(
                payload,
                sender=sender,
            )

        self.assertFalse(result["sent"])
        self.assertEqual(result["reason"], "duplicate_suppressed")
        sender.assert_not_called()

    def test_state_write_failure_does_not_relabel_successful_send(self):
        payload = build_test_payload("SHORT")
        sender = mock.Mock(return_value={"ok": True})
        with mock.patch.object(
            trade_alert,
            "_should_suppress_duplicate",
            return_value={"suppress": False, "cooldown_seconds": 3600},
        ), mock.patch.object(
            trade_alert,
            "_mark_sent",
            side_effect=OSError("read-only"),
        ), mock.patch.object(trade_alert, "_append_log"):
            result = trade_alert.maybe_send_trade_alert(
                payload,
                sender=sender,
            )

        self.assertTrue(result["sent"])
        self.assertFalse(result["state_recorded"])
        self.assertEqual(result["state_error_type"], "OSError")


class TelegramNotifierBoundaryTests(unittest.TestCase):
    def test_empty_message_rejected_without_network(self):
        with mock.patch.object(telegram_notifier.urllib.request, "urlopen") as urlopen:
            result = telegram_notifier.send_telegram_message("")

        self.assertEqual(result["error_code"], "empty_message")
        urlopen.assert_not_called()

    def test_oversize_message_rejected_without_network(self):
        with mock.patch.object(telegram_notifier.urllib.request, "urlopen") as urlopen:
            result = telegram_notifier.send_telegram_message(
                "x" * (telegram_notifier.TELEGRAM_MAX_MESSAGE_LENGTH + 1)
            )

        self.assertEqual(result["error_code"], "message_too_long")
        urlopen.assert_not_called()

    def test_network_exception_does_not_expose_token(self):
        secret = "secret-token-value"
        with mock.patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_TOKEN": secret,
                "TELEGRAM_CHAT_ID": "123",
            },
            clear=False,
        ), mock.patch.object(
            telegram_notifier,
            "_load_env_file",
            return_value={},
        ), mock.patch.object(
            telegram_notifier.urllib.request,
            "urlopen",
            side_effect=RuntimeError(f"https://api.telegram.org/bot{secret}/sendMessage"),
        ):
            result = telegram_notifier.send_telegram_message("테스트")

        self.assertFalse(result["ok"])
        self.assertNotIn(secret, json_like(result))


def json_like(value):
    return repr(value)


if __name__ == "__main__":
    unittest.main()
