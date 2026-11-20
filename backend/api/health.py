"""Health, worker status, and local host metrics endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

from backend.database import utc_now_iso
from backend.schemas import ApiHealthResponse, SystemMetricsResponse, WorkerStatusResponse

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=ApiHealthResponse)
async def get_api_health(request: Request) -> ApiHealthResponse:
    settings = request.app.state.settings
    monitoring = request.app.state.monitoring_service
    return ApiHealthResponse(
        status="ok",
        service="syncpulse-api",
        timestamp_utc=utc_now_iso(),
        max_file_size_bytes=settings.max_file_size_bytes,
        tcp_target=f"{settings.tcp_host}:{settings.tcp_port}",
        udp_listen=f"{settings.udp_host}:{monitoring.bound_udp_port}",
    )


@router.get("/worker/status", response_model=WorkerStatusResponse)
async def get_worker_status(request: Request) -> WorkerStatusResponse:
    monitoring = request.app.state.monitoring_service
    return WorkerStatusResponse(**monitoring.get_worker_status())


@router.get("/system/metrics", response_model=SystemMetricsResponse)
async def get_system_metrics(request: Request) -> SystemMetricsResponse:
    monitoring = request.app.state.monitoring_service
    return SystemMetricsResponse(**monitoring.get_system_metrics())
