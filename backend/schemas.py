"""Pydantic response schemas for SyncPulse REST API."""

from __future__ import annotations

from pydantic import BaseModel


class ApiHealthResponse(BaseModel):
    status: str
    service: str
    timestamp_utc: str
    max_file_size_bytes: int
    tcp_target: str
    udp_listen: str


class WorkerStatusResponse(BaseModel):
    worker_id: str
    liveness_status: str  # STARTING | HEALTHY | UNRESPONSIVE
    is_fresh: bool
    session_id: str | None
    last_sequence_number: int
    last_heartbeat_at: str | None
    started_at: str | None
    uptime_seconds: float | None
    last_heartbeat_interval_ms: float | None
    suspected_missed_heartbeats: int
    total_heartbeats_received: int
    last_transition_event: str | None
    backup_operation_health: str  # OK | DEGRADED | UNKNOWN
    last_transfer_status: str | None
    last_transfer_error_code: str | None


class SystemMetricsResponse(BaseModel):
    scope: str
    cpu_percent: float
    memory_percent: float
    memory_used_mb: float
    memory_total_mb: float
    disk_percent: float
    disk_used_gb: float
    disk_total_gb: float
    collected_at: str


class TransferResponse(BaseModel):
    id: str
    original_filename: str
    file_size: int
    original_sha256: str
    stored_sha256: str | None
    status: str
    bytes_transferred: int
    attempt_count: int
    error_code: str | None
    error_message: str | None
    created_at: str
    started_at: str | None
    completed_at: str | None
    upload_duration_ms: float | None
    tcp_connect_ms: float | None
    transfer_duration_ms: float | None
    total_duration_ms: float | None
    throughput_bytes_per_sec: float | None
    throughput_mb_per_sec: float | None
    can_retry: bool
    can_download: bool


class HealthEventResponse(BaseModel):
    id: int
    worker_id: str
    session_id: str | None
    event_type: str
    previous_status: str | None
    new_status: str | None
    details: str | None
    created_at: str


class MetricsSummaryResponse(BaseModel):
    total_backups: int
    verified_backups: int
    failed_backups: int
    in_progress_backups: int
    transfer_success_rate: float | None
    last_throughput_bytes_per_sec: float | None
    last_throughput_mb_per_sec: float | None
    last_successful_backup_at: str | None
    worker_status: str
    backup_operation_health: str
    last_incident_at: str | None
