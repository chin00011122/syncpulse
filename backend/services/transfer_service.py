"""Transfer orchestration service: HTTP staging, sequential TCP queue, verification, and retry."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from backend.config import Settings
from backend.database import Database, utc_now_iso
from backend.networking.protocol import ProtocolError, validate_transfer_id
from backend.networking.tcp_client import send_file_over_tcp
from backend.services.storage_service import StorageService, sanitize_display_filename

logger = logging.getLogger("syncpulse.transfer_service")


class TransferNotFoundError(Exception):
    pass


class TransferConflictError(Exception):
    pass


class TransferService:
    """
    Coordinates the two-stage backup workflow:
      Stage 1: Save HTTP upload into `data/staging/<transfer_id>.part` + SHA-256
      Stage 2: Enqueue onto a single-concurrency asyncio worker that streams over TCP
               to the Backup Worker process and commits only on verified ACK.
    """

    def __init__(
        self,
        settings: Settings,
        db: Database,
        storage: StorageService,
    ) -> None:
        self.settings = settings
        self.db = db
        self.storage = storage
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._consumer_task: asyncio.Task[None] | None = None
        self._running = False
        self._enqueued_ids: set[str] = set()

    async def start(self) -> None:
        if self._running:
            return
        recovered = self.db.recover_interrupted_transfers()
        if recovered:
            logger.warning(
                "Recovered %d interrupted transfer(s) from previous session as FAILED (retry available)",
                recovered,
            )
        self._running = True
        self._consumer_task = asyncio.create_task(self._consumer_loop(), name="tcp-transfer-consumer")

    async def stop(self) -> None:
        self._running = False
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
            self._consumer_task = None

    async def create_transfer_from_stream(
        self,
        raw_filename: str | None,
        chunks: AsyncIterator[bytes],
    ) -> dict[str, Any]:
        transfer_id = str(uuid.uuid4())
        safe_filename = sanitize_display_filename(raw_filename)
        upload_start = time.perf_counter()

        staging_path, file_size, original_sha256 = await self.storage.save_staged_stream(
            transfer_id=transfer_id,
            chunks=chunks,
        )
        upload_duration_ms = round((time.perf_counter() - upload_start) * 1000.0, 2)
        now = utc_now_iso()

        record = {
            "id": transfer_id,
            "original_filename": safe_filename,
            "file_size": file_size,
            "original_sha256": original_sha256,
            "stored_sha256": None,
            "status": "QUEUED",
            "staging_path": str(staging_path),
            "storage_path": None,
            "bytes_transferred": 0,
            "attempt_count": 0,
            "error_code": None,
            "error_message": None,
            "created_at": now,
            "started_at": None,
            "completed_at": None,
            "upload_duration_ms": upload_duration_ms,
            "tcp_connect_ms": None,
            "transfer_duration_ms": None,
            "total_duration_ms": None,
        }
        self.db.create_transfer(record)
        self._enqueue(transfer_id)
        return self.format_transfer(record)

    def _enqueue(self, transfer_id: str) -> None:
        if transfer_id not in self._enqueued_ids:
            self._enqueued_ids.add(transfer_id)
            self._queue.put_nowait(transfer_id)

    def retry_transfer(self, transfer_id: str) -> dict[str, Any]:
        try:
            safe_id = validate_transfer_id(transfer_id)
        except ProtocolError as exc:
            raise TransferNotFoundError(str(exc)) from exc

        row = self.db.get_transfer(safe_id)
        if row is None:
            raise TransferNotFoundError(f"Transfer {safe_id} not found")

        if row["status"] in ("QUEUED", "TRANSFERRING") or safe_id in self._enqueued_ids:
            raise TransferConflictError("Transfer is already queued or in progress")
        if row["status"] == "VERIFIED":
            raise TransferConflictError("Transfer is already verified and completed")

        staging_path = Path(row["staging_path"])
        if not staging_path.exists():
            raise TransferConflictError("Staged file is no longer available for retry")

        self.db.update_transfer(
            safe_id,
            status="QUEUED",
            bytes_transferred=0,
            error_code=None,
            error_message=None,
            completed_at=None,
        )
        self._enqueue(safe_id)
        updated = self.db.get_transfer(safe_id)
        assert updated is not None
        return self.format_transfer(updated)

    def get_transfer(self, transfer_id: str) -> dict[str, Any]:
        try:
            safe_id = validate_transfer_id(transfer_id)
        except ProtocolError as exc:
            raise TransferNotFoundError(str(exc)) from exc

        row = self.db.get_transfer(safe_id)
        if row is None:
            raise TransferNotFoundError(f"Transfer {safe_id} not found")
        return self.format_transfer(row)

    def list_transfers(self, limit: int = 100) -> list[dict[str, Any]]:
        return [self.format_transfer(r) for r in self.db.list_transfers(limit=limit)]

    def get_download_info(self, transfer_id: str) -> tuple[Path, str]:
        try:
            safe_id = validate_transfer_id(transfer_id)
        except ProtocolError as exc:
            raise TransferNotFoundError(str(exc)) from exc

        row = self.db.get_transfer(safe_id)
        if row is None:
            raise TransferNotFoundError(f"Transfer {safe_id} not found")
        if row["status"] != "VERIFIED":
            raise TransferConflictError(
                f"Transfer is in state {row['status']}; only VERIFIED backups can be downloaded"
            )
        backup_path = self.storage.verify_backup_exists(safe_id)
        if backup_path is None:
            raise TransferNotFoundError("Verified backup file is missing from storage")
        return backup_path, row["original_filename"]

    def format_transfer(self, row: dict[str, Any]) -> dict[str, Any]:
        duration_ms = row.get("transfer_duration_ms")
        file_size = int(row["file_size"])
        throughput_bps: float | None = None
        throughput_mbps: float | None = None
        if row["status"] == "VERIFIED" and duration_ms and duration_ms > 0:
            throughput_bps = round(file_size / (duration_ms / 1000.0), 2)
            throughput_mbps = round(throughput_bps / (1024 * 1024), 3)

        staging_exists = Path(row["staging_path"]).exists() if row.get("staging_path") else False
        can_retry = row["status"] == "FAILED" and staging_exists
        can_download = (
            row["status"] == "VERIFIED"
            and self.storage.verify_backup_exists(row["id"]) is not None
        )

        return {
            "id": row["id"],
            "original_filename": row["original_filename"],
            "file_size": file_size,
            "original_sha256": row["original_sha256"],
            "stored_sha256": row.get("stored_sha256"),
            "status": row["status"],
            "bytes_transferred": int(row.get("bytes_transferred") or 0),
            "attempt_count": int(row.get("attempt_count") or 0),
            "error_code": row.get("error_code"),
            "error_message": row.get("error_message"),
            "created_at": row["created_at"],
            "started_at": row.get("started_at"),
            "completed_at": row.get("completed_at"),
            "upload_duration_ms": row.get("upload_duration_ms"),
            "tcp_connect_ms": row.get("tcp_connect_ms"),
            "transfer_duration_ms": duration_ms,
            "total_duration_ms": row.get("total_duration_ms"),
            "throughput_bytes_per_sec": throughput_bps,
            "throughput_mb_per_sec": throughput_mbps,
            "can_retry": can_retry,
            "can_download": can_download,
        }

    async def _consumer_loop(self) -> None:
        while self._running:
            transfer_id = await self._queue.get()
            try:
                await self._execute_transfer(transfer_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Unhandled error processing transfer %s: %s", transfer_id, exc)
            finally:
                self._enqueued_ids.discard(transfer_id)
                self._queue.task_done()

    async def _execute_transfer(self, transfer_id: str) -> None:
        row = self.db.get_transfer(transfer_id)
        if row is None:
            return

        attempt = int(row["attempt_count"]) + 1
        started_at = utc_now_iso()
        self.db.update_transfer(
            transfer_id,
            status="TRANSFERRING",
            attempt_count=attempt,
            started_at=started_at,
            bytes_transferred=0,
            error_code=None,
            error_message=None,
        )

        last_progress_update = 0.0

        def on_progress(bytes_sent: int, total_bytes: int) -> None:
            nonlocal last_progress_update
            now_mono = time.monotonic()
            if bytes_sent == total_bytes or (now_mono - last_progress_update) >= 0.15:
                last_progress_update = now_mono
                self.db.update_transfer(transfer_id, bytes_transferred=bytes_sent)

        result = await send_file_over_tcp(
            host=self.settings.tcp_host,
            port=self.settings.tcp_port,
            transfer_id=transfer_id,
            original_filename=row["original_filename"],
            file_path=Path(row["staging_path"]),
            expected_sha256=row["original_sha256"],
            chunk_size=self.settings.chunk_size_bytes,
            timeout=self.settings.tcp_timeout_seconds,
            throttle_ms=self.settings.tcp_throttle_ms,
            on_progress=on_progress,
        )

        completed_dt = datetime.now(timezone.utc)
        completed_at = completed_dt.isoformat()
        created_dt = datetime.fromisoformat(row["created_at"])
        total_duration_ms = round((completed_dt - created_dt).total_seconds() * 1000.0, 2)

        if result.success and result.ack is not None:
            backup_path = self.storage.backup_path_for(transfer_id)
            self.db.update_transfer(
                transfer_id,
                status="VERIFIED",
                stored_sha256=result.ack.stored_sha256,
                storage_path=str(backup_path),
                bytes_transferred=result.ack.bytes_written,
                error_code=None,
                error_message=None,
                completed_at=completed_at,
                tcp_connect_ms=round(result.tcp_connect_ms, 2),
                transfer_duration_ms=round(result.transfer_duration_ms, 2),
                total_duration_ms=total_duration_ms,
            )
            self.db.record_health_event(
                worker_id=self.settings.worker_id,
                session_id=None,
                event_type="TRANSFER_VERIFIED",
                previous_status="TRANSFERRING",
                new_status="VERIFIED",
                details=(
                    f"Verified '{row['original_filename']}' ({row['file_size']} B) "
                    f"in {result.transfer_duration_ms:.1f}ms (attempt #{attempt})"
                ),
                created_at=completed_at,
            )
            # Safe to remove staging file ONLY after worker verified & committed backup
            staging_file = Path(row["staging_path"])
            if staging_file.exists():
                try:
                    staging_file.unlink()
                except OSError as exc:
                    logger.warning("Could not remove staging file %s: %s", staging_file, exc)
        else:
            # Preserve staging file so user can retry!
            self.db.update_transfer(
                transfer_id,
                status="FAILED",
                bytes_transferred=result.bytes_sent,
                error_code=result.error_code or "TRANSFER_FAILED",
                error_message=result.error_message or "TCP transfer failed",
                completed_at=completed_at,
                tcp_connect_ms=round(result.tcp_connect_ms, 2),
                transfer_duration_ms=round(result.transfer_duration_ms, 2),
                total_duration_ms=total_duration_ms,
            )
            self.db.record_health_event(
                worker_id=self.settings.worker_id,
                session_id=None,
                event_type="TRANSFER_FAILED",
                previous_status="TRANSFERRING",
                new_status="FAILED",
                details=(
                    f"Failed '{row['original_filename']}' (attempt #{attempt}): "
                    f"[{result.error_code}] {result.error_message}"
                ),
                created_at=completed_at,
            )
