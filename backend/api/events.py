"""Health/incident events and aggregate summary metrics endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from backend.schemas import HealthEventResponse, MetricsSummaryResponse

router = APIRouter(prefix="/api", tags=["events"])


@router.get("/events", response_model=list[HealthEventResponse])
async def list_events(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[HealthEventResponse]:
    db = request.app.state.db
    return [HealthEventResponse(**row) for row in db.list_health_events(limit=limit)]


@router.get("/metrics/summary", response_model=MetricsSummaryResponse)
async def get_metrics_summary(request: Request) -> MetricsSummaryResponse:
    db = request.app.state.db
    monitoring = request.app.state.monitoring_service

    worker_status = monitoring.get_worker_status()
    summary = db.get_transfer_summary()
    last_verified = summary["last_verified"]

    finished_attempts = summary["finished_attempts"]
    verified_count = summary["verified_count"]
    success_rate: float | None = None
    if finished_attempts > 0:
        success_rate = round((verified_count / finished_attempts) * 100.0, 1)

    last_bps: float | None = None
    last_mbps: float | None = None
    last_verified_at: str | None = None
    if last_verified is not None:
        last_verified_at = last_verified["completed_at"]
        dur_ms = last_verified["transfer_duration_ms"]
        if dur_ms and dur_ms > 0:
            last_bps = round(int(last_verified["file_size"]) / (dur_ms / 1000.0), 2)
            last_mbps = round(last_bps / (1024 * 1024), 3)

    return MetricsSummaryResponse(
        total_backups=summary["total_transfers"],
        verified_backups=verified_count,
        failed_backups=summary["failed_count"],
        in_progress_backups=summary["in_progress_count"],
        transfer_success_rate=success_rate,
        last_throughput_bytes_per_sec=last_bps,
        last_throughput_mb_per_sec=last_mbps,
        last_successful_backup_at=last_verified_at,
        worker_status=worker_status["liveness_status"],
        backup_operation_health=worker_status["backup_operation_health"],
        last_incident_at=db.get_last_incident_at(),
    )
