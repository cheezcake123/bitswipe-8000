#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from notifier.alert_ledger_registration import (  # noqa: E402
    DB_PATH_ENV,
    FEATURE_FLAG,
    prepare_trade_alert_registration,
    register_successful_trade_alert,
)
from notifier.telegram_notifier import send_telegram_message  # noqa: E402
from scripts.scenario_ledger import ScenarioLedger  # noqa: E402


TRADE_TEXT = """🟢 [BitSwipe 진입 심사 보고서]

종목: BTCUSDT
최종 판정: 리스크 심사 통과 · 조건부 수동 검토
방향: 롱
신뢰도: 82%

💰 가격 계획
- 현재가: $100.00
- 진입가: $100.00
- 손절가: $95.00
- 1차 목표(1R): $105.00
- 2차 목표(AI 최종 목표): $110.00
- 예상 손익비: 2.00:1
"""

INVALID_TEXT = TRADE_TEXT.replace("- 손절가: $95.00", "- 손절가: $105.00")


class FakeHTTPResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._body


class AlertLedgerRegistrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="bitswipe-alert-ledger-")
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "ledger.sqlite3"
        self.base_env = {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "TELEGRAM_CHAT_ID": "12345",
            DB_PATH_ENV: str(self.db_path),
        }

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def telegram_success(message_id=77):
        return {
            "ok": True,
            "result": {
                "message_id": message_id,
                "chat": {"id": 12345},
            },
        }

    def fake_urlopen(self, captured, response=None):
        response = response or self.telegram_success()

        def _open(request, timeout=10):
            captured["timeout"] = timeout
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeHTTPResponse(response)

        return _open

    def test_01_default_off_does_not_prepare_or_create_db(self):
        env = dict(self.base_env)
        prepared = prepare_trade_alert_registration(TRADE_TEXT, env=env)
        self.assertIsNone(prepared)
        self.assertFalse(self.db_path.exists())

    def test_02_enabled_parses_plan_and_appends_scenario_id(self):
        env = {**self.base_env, FEATURE_FLAG: "1"}
        prepared = prepare_trade_alert_registration(
            TRADE_TEXT,
            env=env,
            scenario_id="BS-TEST-0001",
        )
        self.assertIsNotNone(prepared)
        self.assertEqual(prepared.symbol, "BTCUSDT")
        self.assertEqual(prepared.direction, "LONG")
        self.assertEqual(prepared.entry, 100.0)
        self.assertEqual(prepared.stop, 95.0)
        self.assertEqual(prepared.target_2, 110.0)
        self.assertEqual(prepared.rr, 2.0)
        self.assertIn("시나리오 ID: BS-TEST-0001", prepared.outbound_text)

    def test_03_non_trade_message_is_ignored(self):
        env = {**self.base_env, FEATURE_FLAG: "true"}
        self.assertIsNone(prepare_trade_alert_registration("상태 점검 완료", env=env))

    def test_04_invalid_directional_prices_are_ignored(self):
        env = {**self.base_env, FEATURE_FLAG: "on"}
        self.assertIsNone(prepare_trade_alert_registration(INVALID_TEXT, env=env))

    def test_05_success_registers_planned_scenario(self):
        ledger = ScenarioLedger(self.db_path)
        self.assertTrue(ledger.initialize().ok)
        env = {**self.base_env, FEATURE_FLAG: "1"}
        prepared = prepare_trade_alert_registration(
            TRADE_TEXT,
            env=env,
            scenario_id="BS-TEST-0002",
        )
        result = register_successful_trade_alert(
            prepared,
            self.telegram_success(88),
            db_path=self.db_path,
            env=env,
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["registered"])
        self.assertEqual(result["code"], "CREATED")
        stored = ledger.get_scenario("BS-TEST-0002")
        self.assertTrue(stored.ok)
        self.assertEqual(stored.data["status"], "PLANNED")
        self.assertEqual(stored.data["symbol"], "BTCUSDT")
        self.assertEqual(stored.data["payload"]["telegram_message_id"], 88)

    def test_06_telegram_failure_never_registers(self):
        ledger = ScenarioLedger(self.db_path)
        self.assertTrue(ledger.initialize().ok)
        env = {**self.base_env, FEATURE_FLAG: "1"}
        prepared = prepare_trade_alert_registration(
            TRADE_TEXT,
            env=env,
            scenario_id="BS-TEST-0003",
        )
        result = register_successful_trade_alert(
            prepared,
            {"ok": False, "error": "network"},
            db_path=self.db_path,
            env=env,
        )
        self.assertEqual(result["code"], "TELEGRAM_NOT_OK")
        self.assertEqual(ledger.list_active_scenarios().data, [])

    def test_07_missing_db_does_not_break_success_response(self):
        env = {**self.base_env, FEATURE_FLAG: "1"}
        prepared = prepare_trade_alert_registration(
            TRADE_TEXT,
            env=env,
            scenario_id="BS-TEST-0004",
        )
        result = register_successful_trade_alert(
            prepared,
            self.telegram_success(99),
            db_path=self.db_path,
            env=env,
        )
        self.assertFalse(result["ok"])
        self.assertFalse(result["registered"])
        self.assertEqual(result["code"], "NOT_INITIALIZED")
        self.assertFalse(self.db_path.exists())

    def test_08_duplicate_success_is_idempotent(self):
        ledger = ScenarioLedger(self.db_path)
        self.assertTrue(ledger.initialize().ok)
        env = {**self.base_env, FEATURE_FLAG: "1"}
        prepared = prepare_trade_alert_registration(
            TRADE_TEXT,
            env=env,
            scenario_id="BS-TEST-0005",
        )
        first = register_successful_trade_alert(
            prepared, self.telegram_success(100), db_path=self.db_path, env=env
        )
        second = register_successful_trade_alert(
            prepared, self.telegram_success(100), db_path=self.db_path, env=env
        )
        self.assertEqual(first["code"], "CREATED")
        self.assertEqual(second["code"], "ALREADY_EXISTS")
        self.assertEqual(len(ledger.list_events("BS-TEST-0005").data), 1)

    def test_09_notifier_default_off_keeps_message_and_response_shape(self):
        captured = {}
        env = dict(self.base_env)
        with mock.patch.dict(os.environ, env, clear=True), mock.patch(
            "urllib.request.urlopen", self.fake_urlopen(captured)
        ):
            response = send_telegram_message(TRADE_TEXT)
        self.assertTrue(response["ok"])
        self.assertNotIn("ledger_registration", response)
        self.assertNotIn("시나리오 ID", captured["payload"]["text"])
        self.assertFalse(self.db_path.exists())

    def test_10_notifier_enabled_registers_only_after_http_success(self):
        ledger = ScenarioLedger(self.db_path)
        self.assertTrue(ledger.initialize().ok)
        captured = {}
        env = {**self.base_env, FEATURE_FLAG: "1"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch(
            "urllib.request.urlopen", self.fake_urlopen(captured, self.telegram_success(101))
        ):
            response = send_telegram_message(TRADE_TEXT)
        self.assertTrue(response["ok"])
        self.assertEqual(response["ledger_registration"]["code"], "CREATED")
        self.assertIn("시나리오 ID:", captured["payload"]["text"])
        active = ledger.list_active_scenarios()
        self.assertEqual(len(active.data), 1)
        self.assertEqual(active.data[0]["status"], "PLANNED")

    def test_11_notifier_network_error_does_not_register(self):
        ledger = ScenarioLedger(self.db_path)
        self.assertTrue(ledger.initialize().ok)
        env = {**self.base_env, FEATURE_FLAG: "1"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch(
            "urllib.request.urlopen", side_effect=OSError("offline")
        ):
            response = send_telegram_message(TRADE_TEXT)
        self.assertFalse(response["ok"])
        self.assertEqual(ledger.list_active_scenarios().data, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
