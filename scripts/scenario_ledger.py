#!/usr/bin/env python3
"""Isolated SQLite core for the BitSwipe Scenario Ledger.

Core v0 has no import-time side effects and is not wired to scanners,
Telegram, Binance, systemd, or the existing active_scenarios.json flow.
The default operational database is created only by initialize().
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "scenario_ledger.sqlite3"
SCHEMA_VERSION = 1

PLANNED = "PLANNED"
ACCEPTED = "ACCEPTED"
INVALIDATED = "INVALIDATED"
EXPIRED = "EXPIRED"
ALL_STATUSES = frozenset({PLANNED, ACCEPTED, INVALIDATED, EXPIRED})
ACTIVE_STATUSES = frozenset({PLANNED, ACCEPTED})
ALLOWED_TRANSITIONS = {
    PLANNED: frozenset({ACCEPTED, INVALIDATED, EXPIRED}),
    ACCEPTED: frozenset({INVALIDATED, EXPIRED}),
    INVALIDATED: frozenset(),
    EXPIRED: frozenset(),
}


@dataclass(frozen=True)
class LedgerResult:
    ok: bool
    code: str
    message: str = ""
    data: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class LedgerNotInitializedError(RuntimeError):
    pass


class ScenarioLedger:
    """Transaction-safe, non-throwing repository for scenario state."""

    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB_PATH,
        *,
        busy_timeout_ms: int = 5000,
    ) -> None:
        self.db_path = Path(db_path)
        self.busy_timeout_ms = max(1, int(busy_timeout_ms))

    def initialize(self) -> LedgerResult:
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect(allow_create=True, require_schema=False) as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    version = self._schema_version(conn)
                    if version > SCHEMA_VERSION:
                        conn.rollback()
                        return LedgerResult(
                            False,
                            "SCHEMA_TOO_NEW",
                            f"database schema {version} is newer than {SCHEMA_VERSION}",
                        )
                    if version == 0:
                        self._create_schema_v1(conn)
                        self._verify_schema_v1(conn)
                        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                    elif version == SCHEMA_VERSION:
                        self._verify_schema_v1(conn)
                    else:
                        conn.rollback()
                        return LedgerResult(
                            False,
                            "MIGRATION_UNAVAILABLE",
                            f"no migration path from schema {version}",
                        )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            return LedgerResult(
                True,
                "INITIALIZED",
                data={"db_path": str(self.db_path), "schema_version": SCHEMA_VERSION},
            )
        except (sqlite3.Error, OSError) as exc:
            return self._failure("DB_ERROR", exc)

    def create_scenario(
        self,
        *,
        scenario_id: str,
        source: str,
        symbol: str,
        direction: str,
        payload: Optional[Mapping[str, Any]] = None,
        event_key: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> LedgerResult:
        try:
            scenario_id = self._required(scenario_id, "scenario_id")
            source = self._required(source, "source")
            symbol = self._required(symbol, "symbol").upper()
            direction = self._required(direction, "direction").upper()
            timestamp = created_at or self._now_iso()
            event_key = event_key or f"scenario-created:{scenario_id}"
            payload_json = self._json(payload or {})

            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    existing = self._scenario_row(conn, scenario_id)
                    if existing is not None:
                        conn.rollback()
                        return LedgerResult(
                            True,
                            "ALREADY_EXISTS",
                            data=self._decode_row(existing),
                        )
                    conflict = self._event_row(conn, event_key)
                    if conflict is not None:
                        conn.rollback()
                        return LedgerResult(
                            False,
                            "EVENT_KEY_CONFLICT",
                            data=self._decode_row(conflict),
                        )
                    conn.execute(
                        """
                        INSERT INTO scenarios (
                            scenario_id, status, source, symbol, direction,
                            payload_json, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            scenario_id,
                            PLANNED,
                            source,
                            symbol,
                            direction,
                            payload_json,
                            timestamp,
                            timestamp,
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO scenario_events (
                            event_key, scenario_id, event_type, from_status,
                            to_status, payload_json, created_at
                        ) VALUES (?, ?, 'CREATED', NULL, ?, ?, ?)
                        """,
                        (event_key, scenario_id, PLANNED, payload_json, timestamp),
                    )
                    row = self._scenario_row(conn, scenario_id)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            return LedgerResult(True, "CREATED", data=self._decode_row(row))
        except LedgerNotInitializedError as exc:
            return self._failure("NOT_INITIALIZED", exc)
        except (sqlite3.Error, OSError) as exc:
            return self._failure("DB_ERROR", exc)
        except (TypeError, ValueError) as exc:
            return self._failure("INPUT_ERROR", exc)

    def get_scenario(self, scenario_id: str) -> LedgerResult:
        try:
            scenario_id = self._required(scenario_id, "scenario_id")
            with self._connect() as conn:
                row = self._scenario_row(conn, scenario_id)
            if row is None:
                return LedgerResult(False, "NOT_FOUND", "scenario not found")
            return LedgerResult(True, "FOUND", data=self._decode_row(row))
        except LedgerNotInitializedError as exc:
            return self._failure("NOT_INITIALIZED", exc)
        except (sqlite3.Error, OSError) as exc:
            return self._failure("DB_ERROR", exc)
        except ValueError as exc:
            return self._failure("INPUT_ERROR", exc)

    def list_active_scenarios(self) -> LedgerResult:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM scenarios
                    WHERE status IN ('PLANNED', 'ACCEPTED')
                    ORDER BY created_at, scenario_id
                    """
                ).fetchall()
            return LedgerResult(
                True,
                "LISTED",
                data=[self._decode_row(row) for row in rows],
            )
        except LedgerNotInitializedError as exc:
            return self._failure("NOT_INITIALIZED", exc)
        except (sqlite3.Error, OSError) as exc:
            return self._failure("DB_ERROR", exc)

    def list_events(self, scenario_id: str) -> LedgerResult:
        try:
            scenario_id = self._required(scenario_id, "scenario_id")
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM scenario_events
                    WHERE scenario_id = ? ORDER BY event_id
                    """,
                    (scenario_id,),
                ).fetchall()
            return LedgerResult(
                True,
                "LISTED",
                data=[self._decode_row(row) for row in rows],
            )
        except LedgerNotInitializedError as exc:
            return self._failure("NOT_INITIALIZED", exc)
        except (sqlite3.Error, OSError) as exc:
            return self._failure("DB_ERROR", exc)
        except ValueError as exc:
            return self._failure("INPUT_ERROR", exc)

    def transition(
        self,
        *,
        scenario_id: str,
        to_status: str,
        event_key: str,
        event_type: str = "STATUS_CHANGED",
        payload: Optional[Mapping[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> LedgerResult:
        try:
            scenario_id = self._required(scenario_id, "scenario_id")
            to_status = self._required(to_status, "to_status").upper()
            event_key = self._required(event_key, "event_key")
            event_type = self._required(event_type, "event_type")
            if to_status not in ALL_STATUSES:
                raise ValueError(f"unsupported status: {to_status}")
            timestamp = created_at or self._now_iso()
            payload_json = self._json(payload or {})

            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    existing_event = self._event_row(conn, event_key)
                    if existing_event is not None:
                        same = (
                            existing_event["scenario_id"] == scenario_id
                            and existing_event["to_status"] == to_status
                            and existing_event["event_type"] == event_type
                        )
                        conn.rollback()
                        return LedgerResult(
                            same,
                            "ALREADY_APPLIED" if same else "EVENT_KEY_CONFLICT",
                            data=self._decode_row(existing_event),
                        )

                    current = self._scenario_row(conn, scenario_id)
                    if current is None:
                        conn.rollback()
                        return LedgerResult(False, "NOT_FOUND", "scenario not found")
                    from_status = current["status"]
                    if to_status not in ALLOWED_TRANSITIONS[from_status]:
                        conn.rollback()
                        return LedgerResult(
                            False,
                            "INVALID_TRANSITION",
                            f"{from_status} -> {to_status} is not allowed",
                        )

                    updated = conn.execute(
                        """
                        UPDATE scenarios SET status = ?, updated_at = ?
                        WHERE scenario_id = ? AND status = ?
                        """,
                        (to_status, timestamp, scenario_id, from_status),
                    )
                    if updated.rowcount != 1:
                        raise sqlite3.IntegrityError(
                            "scenario changed concurrently before transition"
                        )
                    conn.execute(
                        """
                        INSERT INTO scenario_events (
                            event_key, scenario_id, event_type, from_status,
                            to_status, payload_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event_key,
                            scenario_id,
                            event_type,
                            from_status,
                            to_status,
                            payload_json,
                            timestamp,
                        ),
                    )
                    row = self._scenario_row(conn, scenario_id)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            return LedgerResult(True, "TRANSITIONED", data=self._decode_row(row))
        except LedgerNotInitializedError as exc:
            return self._failure("NOT_INITIALIZED", exc)
        except (sqlite3.Error, OSError) as exc:
            return self._failure("DB_ERROR", exc)
        except (TypeError, ValueError) as exc:
            return self._failure("INPUT_ERROR", exc)

    def inspect_database(self) -> LedgerResult:
        try:
            with self._connect() as conn:
                tables = sorted(
                    row["name"]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                )
                data = {
                    "schema_version": self._schema_version(conn),
                    "journal_mode": conn.execute("PRAGMA journal_mode").fetchone()[0],
                    "foreign_keys": conn.execute("PRAGMA foreign_keys").fetchone()[0],
                    "busy_timeout": conn.execute("PRAGMA busy_timeout").fetchone()[0],
                    "synchronous": conn.execute("PRAGMA synchronous").fetchone()[0],
                    "tables": tables,
                }
            return LedgerResult(True, "INSPECTED", data=data)
        except LedgerNotInitializedError as exc:
            return self._failure("NOT_INITIALIZED", exc)
        except (sqlite3.Error, OSError) as exc:
            return self._failure("DB_ERROR", exc)

    @contextmanager
    def _connect(
        self,
        *,
        allow_create: bool = False,
        require_schema: bool = True,
    ) -> Iterator[sqlite3.Connection]:
        if not allow_create and not self.db_path.exists():
            raise LedgerNotInitializedError(
                "ledger is not initialized; call initialize() explicitly"
            )
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=self.busy_timeout_ms / 1000.0,
            isolation_level=None,
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = FULL")
            if require_schema and self._schema_version(conn) != SCHEMA_VERSION:
                raise LedgerNotInitializedError(
                    f"expected schema {SCHEMA_VERSION}; call initialize() explicitly"
                )
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _schema_version(conn: sqlite3.Connection) -> int:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])

    @staticmethod
    def _create_schema_v1(conn: sqlite3.Connection) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS scenarios (
                scenario_id TEXT PRIMARY KEY,
                status TEXT NOT NULL CHECK (
                    status IN ('PLANNED','ACCEPTED','INVALIDATED','EXPIRED')
                ),
                source TEXT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS scenario_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL UNIQUE,
                scenario_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT NOT NULL CHECK (
                    to_status IN ('PLANNED','ACCEPTED','INVALIDATED','EXPIRED')
                ),
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY (scenario_id) REFERENCES scenarios(scenario_id)
                    ON DELETE CASCADE
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_scenarios_status_created
            ON scenarios(status, created_at)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_events_scenario
            ON scenario_events(scenario_id, event_id)
            """,
        )
        for statement in statements:
            conn.execute(statement)

    @staticmethod
    def _verify_schema_v1(conn: sqlite3.Connection) -> None:
        required = {
            "scenarios": {
                "scenario_id",
                "status",
                "source",
                "symbol",
                "direction",
                "payload_json",
                "created_at",
                "updated_at",
            },
            "scenario_events": {
                "event_id",
                "event_key",
                "scenario_id",
                "event_type",
                "from_status",
                "to_status",
                "payload_json",
                "created_at",
            },
        }
        for table, columns in required.items():
            actual = {
                row[1]
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            missing = columns - actual
            if missing:
                raise sqlite3.DatabaseError(
                    f"schema v1 table {table} missing columns: {sorted(missing)}"
                )

    @staticmethod
    def _scenario_row(conn: sqlite3.Connection, scenario_id: str):
        return conn.execute(
            "SELECT * FROM scenarios WHERE scenario_id = ?", (scenario_id,)
        ).fetchone()

    @staticmethod
    def _event_row(conn: sqlite3.Connection, event_key: str):
        return conn.execute(
            "SELECT * FROM scenario_events WHERE event_key = ?", (event_key,)
        ).fetchone()

    @staticmethod
    def _required(value: object, field: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{field} is required")
        return text

    @staticmethod
    def _json(value: Mapping[str, Any]) -> str:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=lambda item: item.isoformat()
            if hasattr(item, "isoformat")
            else str(item),
        )

    @staticmethod
    def _decode_row(row) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        result = dict(row)
        if "payload_json" in result:
            try:
                result["payload"] = json.loads(result.pop("payload_json"))
            except (TypeError, json.JSONDecodeError):
                result["payload"] = {}
        return result

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")

    @staticmethod
    def _failure(code: str, exc: Exception) -> LedgerResult:
        return LedgerResult(False, code, f"{type(exc).__name__}: {exc}")


__all__ = [
    "ACCEPTED",
    "ACTIVE_STATUSES",
    "ALL_STATUSES",
    "ALLOWED_TRANSITIONS",
    "DEFAULT_DB_PATH",
    "EXPIRED",
    "INVALIDATED",
    "LedgerResult",
    "PLANNED",
    "SCHEMA_VERSION",
    "ScenarioLedger",
]
