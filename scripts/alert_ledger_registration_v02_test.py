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
    REGISTRATION_VERSION,
    prepare_trade_alert_registration,
    register_successful_trade_alert,
)
from notifier.telegram_notifier import send_telegram_message  # noqa: E402
from scripts.scenario_ledger import ScenarioLedger  # noqa: E402

try:
    import notifier.trade_alert as trade_alert_module  # noqa: E402
except Exception:
    trade_alert_module = None

LOW_TEXT = """🟢 [BitSwipe 진입 심사 보고서]

종목: LOWUSDT
방향: 롱
신뢰도: 84%

💰 가격 계획
- 진입가: $0.00
- 손절가: $0.00
- 1차 목표(1R): $0.00
- 2차 목표(AI 최종 목표): $0.00
- 예상 손익비: 2.10:1
"""

EXACT_PLAN = {
    "symbol": "LOWUSDT",
    "direction": "LONG",
    "entry": 0.003421,
    "stop": 0.003200,
    "target_1": 0.003642,
    "target_2": 0.003885,
    "rr": 2.10,
    "confidence": 84,
}


class FakeHTTPResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.body


class AlertRegistrationV02Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="bitswipe-alert-v02-")
        self.db_path = Path(self.tmp.name) / "ledger.sqlite3"
        self.env = {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "TELEGRAM_CHAT_ID": "12345",
            DB_PATH_ENV: str(self.db_path),
        }

    def tearDown(self):
        self.tmp.cleanup()

    def initialize(self):
        result = ScenarioLedger(self.db_path).initialize()
        self.assertTrue(result.ok, result.to_dict())

    @staticmethod
    def telegram_success(message_id=77):
        return {"ok": True, "result": {"message_id": message_id, "chat": {"id": 12345}}}

    def fake_urlopen(self, captured, response=None):
        response = response or self.telegram_success()

        def _open(request, timeout=10):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeHTTPResponse(response)

        return _open

    def test_01_structured_plan_preserves_sub_cent_prices(self):
        self.initialize()
        env = {**self.env, FEATURE_FLAG: "1"}
        prepared = prepare_trade_alert_registration(
            LOW_TEXT, plan=EXACT_PLAN, env=env, scenario_id="BS-LOW-1"
        )
        self.assertIsNotNone(prepared)
        self.assertEqual(prepared.source_mode, "structured_plan")
        self.assertEqual(prepared.entry, 0.003421)
        self.assertEqual(prepared.stop, 0.0032)
        self.assertEqual(prepared.target_2, 0.003885)

    def test_02_success_stores_exact_structured_values(self):
        self.initialize()
        env = {**self.env, FEATURE_FLAG: "1"}
        prepared = prepare_trade_alert_registration(
            LOW_TEXT, plan=EXACT_PLAN, env=env, scenario_id="BS-LOW-2"
        )
        result = register_successful_trade_alert(
            prepared, self.telegram_success(88), db_path=self.db_path, env=env
        )
        self.assertTrue(result["registered"], result)
        stored = ScenarioLedger(self.db_path).get_scenario("BS-LOW-2")
        payload = stored.data["payload"]
        self.assertEqual(payload["entry"], 0.003421)
        self.assertEqual(payload["target"], 0.003885)
        self.assertEqual(payload["registration_version"], REGISTRATION_VERSION)
        self.assertEqual(payload["registration_source_mode"], "structured_plan")

    def test_03_default_off_keeps_notifier_unchanged(self):
        captured = {}
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch(
            "urllib.request.urlopen", self.fake_urlopen(captured)
        ):
            response = send_telegram_message(LOW_TEXT, registration_plan=EXACT_PLAN)
        self.assertTrue(response["ok"])
        self.assertNotIn("ledger_registration", response)
        self.assertNotIn("시나리오 ID", captured["payload"]["text"])
        self.assertFalse(self.db_path.exists())

    def test_04_notifier_registers_only_after_success(self):
        self.initialize()
        captured = {}
        env = {**self.env, FEATURE_FLAG: "1"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch(
            "urllib.request.urlopen", self.fake_urlopen(captured, self.telegram_success(99))
        ):
            response = send_telegram_message(LOW_TEXT, registration_plan=EXACT_PLAN)
        self.assertEqual(response["ledger_registration"]["code"], "CREATED")
        self.assertIn("시나리오 ID:", captured["payload"]["text"])
        active = ScenarioLedger(self.db_path).list_active_scenarios().data
        self.assertEqual(active[0]["payload"]["entry"], 0.003421)

    def test_05_invalid_structured_plan_fails_closed(self):
        self.initialize()
        env = {**self.env, FEATURE_FLAG: "1"}
        invalid = {**EXACT_PLAN, "stop": 0.0038}
        self.assertIsNone(prepare_trade_alert_registration(LOW_TEXT, plan=invalid, env=env))

    def test_06_missing_db_does_not_annotate_or_create(self):
        env = {**self.env, FEATURE_FLAG: "1"}
        self.assertIsNone(prepare_trade_alert_registration(LOW_TEXT, plan=EXACT_PLAN, env=env))
        self.assertFalse(self.db_path.exists())

    def test_07_plan_mapping_is_not_mutated(self):
        self.initialize()
        env = {**self.env, FEATURE_FLAG: "1"}
        original = dict(EXACT_PLAN)
        self.assertIsNotNone(prepare_trade_alert_registration(LOW_TEXT, plan=original, env=env))
        self.assertEqual(original, EXACT_PLAN)

    @unittest.skipIf(trade_alert_module is None, "complete repository required")
    def test_08_trade_alert_passes_exact_raw_plan(self):
        payload = {
            "symbol": "LOWUSDT",
            "pair_label": "LOWUSDT",
            "signal": "buy",
            "confidence": 84,
            "price": 0.0034,
            "risk_guard": {
                "verdict": "PASS",
                "entry_price": 0.003421,
                "stop_price": 0.0032,
                "target_price": 0.003885,
                "risk_reward_ratio": 2.1,
                "stop_distance_percent": 6.46,
                "leverage": 2,
                "leveraged_loss_percent_on_margin": 12.92,
                "max_position_percent_by_account_risk": 15.48,
            },
            "analysis_json": {},
            "report_sections": {},
        }
        with mock.patch.object(
            trade_alert_module, "_should_suppress_duplicate", return_value={"suppress": False}
        ), mock.patch.object(trade_alert_module, "_mark_sent"), mock.patch.object(
            trade_alert_module, "_append_log"
        ), mock.patch.object(
            trade_alert_module, "_append_final_verdict_log"
        ), mock.patch.object(
            trade_alert_module,
            "send_telegram_message",
            return_value={"ok": True, "result": {"message_id": 500}},
        ) as sender:
            result = trade_alert_module.maybe_send_trade_alert(payload)
        self.assertTrue(result["sent"])
        plan = sender.call_args.kwargs["registration_plan"]
        self.assertEqual(plan["entry"], 0.003421)
        self.assertEqual(plan["stop"], 0.0032)
        self.assertEqual(plan["target_2"], 0.003885)
        self.assertEqual(plan["direction"], "LONG")


if __name__ == "__main__":
    unittest.main(verbosity=2)
