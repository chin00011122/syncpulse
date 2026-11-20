"""Infrastructure monitoring service combining UDP heartbeats and host system metrics."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil

from backend.config import Settings
from backend.database import Database
from backend.networking.udp_heartbeat import (
    HeartbeatStateMachine,
    HealthTransitionEvent,
    UDPHeartbeatReceiver,
    WorkerHealthSnapshot,
)

logger = logging.getLogger("syncpulse.monitoring")


class MonitoringService:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        # Prime psutil cpu_percent so subsequent non-blocking calls return real deltas
        psutil.cpu_percent(interval=None)

        self.state_machine = HeartbeatStateMachine(
            worker_id=settings.worker_id,
            timeout_seconds=settings.heartbeat_timeout_seconds,
            on_transition=self._handle_transition,
            on_heartbeat=self._handle_heartbeat,
        )
        self.receiver = UDPHeartbeatReceiver(
            host=settings.udp_host,
            port=settings.udp_port,
            state_machine=self.state_machine,
        )

    async def start(self) -> None:
        await self.receiver.start()

    async def stop(self) -> None:
        await self.receiver.stop()

    @property
    def bound_udp_port(self) -> int:
        return self.receiver.bound_port

    def _handle_heartbeat(self, snap: WorkerHealthSnapshot) -> None:
        try:
            self.db.upsert_worker_state(
                worker_id=snap.worker_id,
                session_id=snap.session_id,
                current_status=snap.status,
                last_sequence_number=snap.last_sequence_number,
                last_seen_at=snap.last_seen_at,
                started_at=snap.started_at,
                updated_at=snap.updated_at,
            )
        except Exception as exc:
            logger.warning("Failed to persist worker_state: %s", exc)

    def _handle_transition(
        self,
        event: HealthTransitionEvent,
        snap: WorkerHealthSnapshot,
    ) -> None:
        logger.info(
            "Worker state transition: %s (%s -> %s) | %s",
            event.event_type,
            event.previous_status,
            event.new_status,
            event.details,
        )
        try:
            self.db.upsert_worker_state(
                worker_id=snap.worker_id,
                session_id=snap.session_id,
                current_status=snap.status,
                last_sequence_number=snap.last_sequence_number,
                last_seen_at=snap.last_seen_at,
                started_at=snap.started_at,
                updated_at=snap.updated_at,
            )
            self.db.record_health_event(
                worker_id=event.worker_id,
                session_id=event.session_id,
                event_type=event.event_type,
                previous_status=event.previous_status,
                new_status=event.new_status,
                details=event.details,
                created_at=event.created_at,
            )
        except Exception as exc:
            logger.warning("Failed to persist health transition event: %s", exc)

    def get_worker_status(self) -> dict[str, Any]:
        # Evaluate timeout immediately before returning status
        self.state_machine.check_timeout()
        snap = self.state_machine.snapshot
        summary = self.db.get_transfer_summary()
        last_transfer = summary["last_transfer"]

        is_fresh = False
        if snap.last_seen_monotonic is not None:
            is_fresh = (time.monotonic() - snap.last_seen_monotonic) <= self.settings.heartbeat_timeout_seconds

        if last_transfer is None:
            backup_op_health = "UNKNOWN"
            last_transfer_status = None
            last_transfer_error_code = None
        else:
            last_transfer_status = last_transfer["status"]
            last_transfer_error_code = last_transfer["error_code"]
            if last_transfer_status == "FAILED":
                backup_op_health = "DEGRADED"
            elif last_transfer_status == "VERIFIED":
                backup_op_health = "OK"
            else:
                backup_op_health = "OK" if summary["failed_count"] == 0 else "DEGRADED"

        return {
            "worker_id": snap.worker_id,
            "liveness_status": snap.status,
            "is_fresh": is_fresh,
            "session_id": snap.session_id,
            "last_sequence_number": snap.last_sequence_number,
            "last_heartbeat_at": snap.last_seen_at,
            "started_at": snap.started_at,
            "uptime_seconds": snap.uptime_seconds,
            "last_heartbeat_interval_ms": snap.last_interval_ms,
            "suspected_missed_heartbeats": snap.suspected_missed_heartbeats,
            "total_heartbeats_received": snap.total_heartbeats_received,
            "last_transition_event": snap.last_transition_event,
            "backup_operation_health": backup_op_health,
            "last_transfer_status": last_transfer_status,
            "last_transfer_error_code": last_transfer_error_code,
        }

    def get_system_metrics(self) -> dict[str, Any]:
        mem = psutil.virtual_memory()
        disk_target = str(Path(self.settings.data_path).anchor or self.settings.data_path)
        disk = psutil.disk_usage(disk_target)
        return {
            "scope": "local_host",
            "cpu_percent": round(float(psutil.cpu_percent(interval=None)), 1),
            "memory_percent": round(float(mem.percent), 1),
            "memory_used_mb": round(mem.used / (1024 * 1024), 1),
            "memory_total_mb": round(mem.total / (1024 * 1024), 1),
            "disk_percent": round(float(disk.percent), 1),
            "disk_used_gb": round(disk.used / (1024 * 1024 * 1024), 2),
            "disk_total_gb": round(disk.total / (1024 * 1024 * 1024), 2),
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }
