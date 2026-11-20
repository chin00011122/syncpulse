"""SQLite persistence layer for SyncPulse (API Server is the single writer)."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS transfers (
    id TEXT PRIMARY KEY,
    original_filename TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    original_sha256 TEXT NOT NULL,
    stored_sha256 TEXT,
    status TEXT NOT NULL,
    staging_path TEXT NOT NULL,
    storage_path TEXT,
    bytes_transferred INTEGER NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    upload_duration_ms REAL,
    tcp_connect_ms REAL,
    transfer_duration_ms REAL,
    total_duration_ms REAL
);

CREATE TABLE IF NOT EXISTS health_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_id TEXT NOT NULL,
    session_id TEXT,
    event_type TEXT NOT NULL,
    previous_status TEXT,
    new_status TEXT,
    details TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS worker_state (
    worker_id TEXT PRIMARY KEY,
    session_id TEXT,
    current_status TEXT NOT NULL,
    last_sequence_number INTEGER NOT NULL DEFAULT 0,
    last_seen_at TEXT,
    started_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_transfers_created_at ON transfers(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_health_events_created_at ON health_events(created_at DESC);
"""


class Database:
    """Thread-safe SQLite wrapper using WAL mode and explicit transactions."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.RLock()

    def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=15.0)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA foreign_keys=ON;")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    # ── Transfers ────────────────────────────────────────────────────────────

    def create_transfer(self, record: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO transfers (
                    id, original_filename, file_size, original_sha256, stored_sha256,
                    status, staging_path, storage_path, bytes_transferred, attempt_count,
                    error_code, error_message, created_at, started_at, completed_at,
                    upload_duration_ms, tcp_connect_ms, transfer_duration_ms, total_duration_ms
                ) VALUES (
                    :id, :original_filename, :file_size, :original_sha256, :stored_sha256,
                    :status, :staging_path, :storage_path, :bytes_transferred, :attempt_count,
                    :error_code, :error_message, :created_at, :started_at, :completed_at,
                    :upload_duration_ms, :tcp_connect_ms, :transfer_duration_ms, :total_duration_ms
                )
                """,
                record,
            )

    def update_transfer(self, transfer_id: str, **fields: Any) -> None:
        if not fields:
            return
        columns = ", ".join(f"{key} = :{key}" for key in fields)
        params = dict(fields)
        params["id"] = transfer_id
        with self.connect() as conn:
            conn.execute(f"UPDATE transfers SET {columns} WHERE id = :id", params)

    def get_transfer(self, transfer_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM transfers WHERE id = ?", (transfer_id,)).fetchone()
            return dict(row) if row is not None else None

    def list_transfers(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM transfers ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def recover_interrupted_transfers(self) -> int:
        """Mark any leftover QUEUED/TRANSFERRING rows from a previous crash as FAILED so they can be retried."""
        now = utc_now_iso()
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE transfers
                SET status = 'FAILED',
                    error_code = 'API_RESTART_INTERRUPTED',
                    error_message = 'API server restarted before TCP backup completed; retry available',
                    completed_at = ?
                WHERE status IN ('QUEUED', 'TRANSFERRING')
                """,
                (now,),
            )
            return cur.rowcount

    def get_transfer_summary(self) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_transfers,
                    SUM(CASE WHEN status = 'VERIFIED' THEN 1 ELSE 0 END) AS verified_count,
                    SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) AS failed_count,
                    SUM(CASE WHEN status IN ('STAGED', 'QUEUED', 'TRANSFERRING') THEN 1 ELSE 0 END) AS in_progress_count,
                    COALESCE(SUM(CASE WHEN status IN ('VERIFIED', 'FAILED') THEN attempt_count ELSE 0 END), 0) AS finished_attempts
                FROM transfers
                """
            ).fetchone()

            last_verified = conn.execute(
                """
                SELECT id, original_filename, file_size, completed_at, transfer_duration_ms
                FROM transfers
                WHERE status = 'VERIFIED'
                ORDER BY completed_at DESC
                LIMIT 1
                """
            ).fetchone()

            last_transfer = conn.execute(
                """
                SELECT id, original_filename, status, error_code, completed_at, created_at
                FROM transfers
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()

            return {
                "total_transfers": int(row["total_transfers"] or 0),
                "verified_count": int(row["verified_count"] or 0),
                "failed_count": int(row["failed_count"] or 0),
                "in_progress_count": int(row["in_progress_count"] or 0),
                "finished_attempts": int(row["finished_attempts"] or 0),
                "last_verified": dict(last_verified) if last_verified else None,
                "last_transfer": dict(last_transfer) if last_transfer else None,
            }

    # ── Health Events & Worker State ─────────────────────────────────────────

    def record_health_event(
        self,
        worker_id: str,
        session_id: str | None,
        event_type: str,
        previous_status: str | None,
        new_status: str | None,
        details: str,
        created_at: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO health_events (
                    worker_id, session_id, event_type, previous_status,
                    new_status, details, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    worker_id,
                    session_id,
                    event_type,
                    previous_status,
                    new_status,
                    details,
                    created_at or utc_now_iso(),
                ),
            )

    def list_health_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM health_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_last_incident_at(self) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT created_at
                FROM health_events
                WHERE event_type IN ('WORKER_UNRESPONSIVE', 'TRANSFER_FAILED')
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            return str(row["created_at"]) if row else None

    def upsert_worker_state(
        self,
        worker_id: str,
        session_id: str | None,
        current_status: str,
        last_sequence_number: int,
        last_seen_at: str | None,
        started_at: str | None,
        updated_at: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO worker_state (
                    worker_id, session_id, current_status, last_sequence_number,
                    last_seen_at, started_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    current_status = excluded.current_status,
                    last_sequence_number = excluded.last_sequence_number,
                    last_seen_at = excluded.last_seen_at,
                    started_at = excluded.started_at,
                    updated_at = excluded.updated_at
                """,
                (
                    worker_id,
                    session_id,
                    current_status,
                    last_sequence_number,
                    last_seen_at,
                    started_at,
                    updated_at,
                ),
            )
