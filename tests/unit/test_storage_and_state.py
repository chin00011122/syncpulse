"""Unit tests for safe storage path handling and SQLite startup recovery."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from backend.database import Database, utc_now_iso
from backend.networking.protocol import ProtocolError
from backend.services.storage_service import (
    FileTooLargeError,
    StorageService,
    sanitize_display_filename,
)


def test_sanitize_display_filename_strips_paths_and_unsafe_chars() -> None:
    assert sanitize_display_filename("../../secret.txt") == "secret.txt"
    assert sanitize_display_filename("C:\\Users\\test\\photo?.jpg") == "photo_.jpg"
    assert sanitize_display_filename("") == "unnamed.bin"
    assert sanitize_display_filename("..") == "unnamed.bin"


def test_storage_service_blocks_path_traversal(tmp_path: Path) -> None:
    storage = StorageService(tmp_path / "staging", tmp_path / "backups", max_file_size_bytes=1024)
    with pytest.raises(ProtocolError):
        storage.staging_path_for("../../evil")
    with pytest.raises(ProtocolError):
        storage.backup_path_for("..\\evil")


async def test_save_staged_stream_enforces_size_limit_and_cleans_up(tmp_path: Path) -> None:
    storage = StorageService(tmp_path / "staging", tmp_path / "backups", max_file_size_bytes=10)
    tid = str(uuid.uuid4())

    async def oversize_chunks():
        yield b"123456"
        yield b"789012"

    with pytest.raises(FileTooLargeError):
        await storage.save_staged_stream(tid, oversize_chunks())

    assert not storage.staging_path_for(tid).exists()


def test_database_recovers_interrupted_transfers_on_startup(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.init()
    tid = str(uuid.uuid4())
    db.create_transfer(
        {
            "id": tid,
            "original_filename": "doc.pdf",
            "file_size": 100,
            "original_sha256": "a" * 64,
            "stored_sha256": None,
            "status": "TRANSFERRING",
            "staging_path": str(tmp_path / f"{tid}.part"),
            "storage_path": None,
            "bytes_transferred": 50,
            "attempt_count": 1,
            "error_code": None,
            "error_message": None,
            "created_at": utc_now_iso(),
            "started_at": utc_now_iso(),
            "completed_at": None,
            "upload_duration_ms": 10.0,
            "tcp_connect_ms": None,
            "transfer_duration_ms": None,
            "total_duration_ms": None,
        }
    )

    recovered = db.recover_interrupted_transfers()
    assert recovered == 1
    row = db.get_transfer(tid)
    assert row is not None
    assert row["status"] == "FAILED"
    assert row["error_code"] == "API_RESTART_INTERRUPTED"
