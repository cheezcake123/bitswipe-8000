#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from scenario_ledger import ACCEPTED, EXPIRED, INVALIDATED, ScenarioLedger


class ScenarioLedgerCoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="bitswipe-ledger-test-")
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "isolated" / "ledger.sqlite3"
        self.ledger = ScenarioLedger(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def init(self):
        result = self.ledger.initialize()
        self.assertTrue(result.ok, result.to_dict())

    def create(self, scenario_id="SCN-1", event_key=None):
        return self.ledger.create_scenario(
            scenario_id=scenario_id,
            source="unit-test",
            symbol="BTCUSDT",
            direction="LONG",
            payload={"entry": 100, "stop": 95, "target": 110},
            event_key=event_key,
        )

    def test_01_no_file_before_initialize(self):
        self.assertFalse(self.db_path.exists())
        result = self.ledger.get_scenario("SCN-1")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "NOT_INITIALIZED")
        self.assertFalse(self.db_path.exists())

    def test_02_initialize_schema_and_pragmas(self):
        self.init()
        info = self.ledger.inspect_database()
        self.assertTrue(info.ok)
        self.assertEqual(info.data["schema_version"], 1)
        self.assertEqual(str(info.data["journal_mode"]).lower(), "wal")
        self.assertEqual(info.data["foreign_keys"], 1)
        self.assertEqual(info.data["busy_timeout"], 5000)
        self.assertIn("scenarios", info.data["tables"])
        self.assertIn("scenario_events", info.data["tables"])

    def test_03_duplicate_scenario_is_idempotent(self):
        self.init()
        first = self.create()
        second = self.create()
        self.assertEqual(first.code, "CREATED")
        self.assertEqual(second.code, "ALREADY_EXISTS")
        events = self.ledger.list_events("SCN-1")
        self.assertEqual(len(events.data), 1)

    def test_04_duplicate_transition_event_is_idempotent(self):
        self.init()
        self.create()
        first = self.ledger.transition(
            scenario_id="SCN-1", to_status=ACCEPTED, event_key="tg:100"
        )
        second = self.ledger.transition(
            scenario_id="SCN-1", to_status=ACCEPTED, event_key="tg:100"
        )
        self.assertEqual(first.code, "TRANSITIONED")
        self.assertTrue(second.ok)
        self.assertEqual(second.code, "ALREADY_APPLIED")
        self.assertEqual(len(self.ledger.list_events("SCN-1").data), 2)

    def test_05_event_key_conflict_is_rejected(self):
        self.init()
        self.create("SCN-1")
        self.create("SCN-2")
        self.ledger.transition(
            scenario_id="SCN-1", to_status=ACCEPTED, event_key="shared"
        )
        conflict = self.ledger.transition(
            scenario_id="SCN-2", to_status=ACCEPTED, event_key="shared"
        )
        self.assertFalse(conflict.ok)
        self.assertEqual(conflict.code, "EVENT_KEY_CONFLICT")
        self.assertEqual(self.ledger.get_scenario("SCN-2").data["status"], "PLANNED")

    def test_06_allowed_transitions(self):
        for target in (ACCEPTED, INVALIDATED, EXPIRED):
            db = self.root / f"{target}.sqlite3"
            ledger = ScenarioLedger(db)
            self.assertTrue(ledger.initialize().ok)
            self.assertTrue(
                ledger.create_scenario(
                    scenario_id="S",
                    source="test",
                    symbol="ETHUSDT",
                    direction="SHORT",
                ).ok
            )
            result = ledger.transition(
                scenario_id="S", to_status=target, event_key=f"to:{target}"
            )
            self.assertTrue(result.ok, result.to_dict())

    def test_07_invalid_transition_rolls_back(self):
        self.init()
        self.create()
        self.ledger.transition(
            scenario_id="SCN-1", to_status=ACCEPTED, event_key="accept"
        )
        rejected = self.ledger.transition(
            scenario_id="SCN-1", to_status="PLANNED", event_key="rewind"
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(rejected.code, "INVALID_TRANSITION")
        self.assertEqual(self.ledger.get_scenario("SCN-1").data["status"], ACCEPTED)
        self.assertEqual(len(self.ledger.list_events("SCN-1").data), 2)

    def test_08_terminal_state_not_active(self):
        self.init()
        self.create("ACTIVE")
        self.create("DONE")
        self.ledger.transition(
            scenario_id="DONE", to_status=EXPIRED, event_key="expire:DONE"
        )
        active = self.ledger.list_active_scenarios()
        self.assertEqual([row["scenario_id"] for row in active.data], ["ACTIVE"])

    def test_09_persists_after_reopen(self):
        self.init()
        self.create()
        reopened = ScenarioLedger(self.db_path)
        result = reopened.get_scenario("SCN-1")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["symbol"], "BTCUSDT")

    def test_10_two_connections_do_not_duplicate(self):
        self.init()
        other = ScenarioLedger(self.db_path)
        first = self.create()
        second = other.create_scenario(
            scenario_id="SCN-1",
            source="other",
            symbol="BTCUSDT",
            direction="LONG",
        )
        self.assertEqual(first.code, "CREATED")
        self.assertEqual(second.code, "ALREADY_EXISTS")

    def test_11_db_error_is_returned_not_raised(self):
        bad_path = self.root / "directory-as-db"
        bad_path.mkdir()
        ledger = ScenarioLedger(bad_path)
        result = ledger.initialize()
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "DB_ERROR")

    def test_12_does_not_touch_unrelated_data_or_logs(self):
        data = self.root / "data"
        logs = self.root / "logs"
        data.mkdir()
        logs.mkdir()
        data_sentinel = data / "sentinel.txt"
        log_sentinel = logs / "sentinel.txt"
        data_sentinel.write_text("data", encoding="utf-8")
        log_sentinel.write_text("logs", encoding="utf-8")
        self.init()
        self.create()
        self.assertEqual(data_sentinel.read_text(encoding="utf-8"), "data")
        self.assertEqual(log_sentinel.read_text(encoding="utf-8"), "logs")
        self.assertEqual(sorted(p.name for p in data.iterdir()), ["sentinel.txt"])
        self.assertEqual(sorted(p.name for p in logs.iterdir()), ["sentinel.txt"])

    def test_13_corrupt_declared_schema_is_rejected(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()
        result = self.ledger.initialize()
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "DB_ERROR")


if __name__ == "__main__":
    unittest.main(verbosity=2)
