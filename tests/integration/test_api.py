"""End-to-end HTTP API integration tests backed by real TCP Backup Server and UDP Heartbeat."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import httpx
import pytest

from backend.app import create_app
from backend.config import Settings
from backend.networking.tcp_server import TCPBackupServer
from backend.networking.udp_heartbeat import UDPHeartbeatSender


async def _wait_for_transfer_status(
    client: httpx.AsyncClient,
    transfer_id: str,
    target_statuses: set[str],
    timeout: float = 5.0,
) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/api/transfers/{transfer_id}")
        resp.raise_for_status()
        data = resp.json()
        if data["status"] in target_statuses:
            return data
        await asyncio.sleep(0.05)
    raise AssertionError(f"Transfer {transfer_id} did not reach {target_statuses} within {timeout}s")


@pytest.fixture
async def live_env(tmp_path: Path):
    backups_dir = tmp_path / "data" / "backups"
    tcp_server = TCPBackupServer(
        host="127.0.0.1",
        port=0,
        backups_dir=backups_dir,
        max_file_size=256 * 1024,  # 256 KiB limit for tests
        chunk_size=16 * 1024,
        timeout=5.0,
    )
    await tcp_server.start()

    settings = Settings(
        data_dir=str(tmp_path / "data"),
        tcp_host="127.0.0.1",
        tcp_port=tcp_server.bound_port,
        udp_host="127.0.0.1",
        udp_port=0,  # OS assigns ephemeral UDP port
        max_file_size_bytes=256 * 1024,
        chunk_size_bytes=16 * 1024,
        tcp_timeout_seconds=5.0,
        heartbeat_interval_seconds=0.1,
        heartbeat_timeout_seconds=0.5,
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        udp_port = app.state.monitoring_service.bound_udp_port
        hb_sender = UDPHeartbeatSender(
            target_host="127.0.0.1",
            target_port=udp_port,
            worker_id=settings.worker_id,
            interval_seconds=0.1,
        )
        await hb_sender.start()
        await asyncio.sleep(0.2)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield {
                "client": client,
                "app": app,
                "tcp_server": tcp_server,
                "hb_sender": hb_sender,
                "settings": settings,
            }

        await hb_sender.stop()
    await tcp_server.stop()


async def test_health_worker_and_metrics_endpoints(live_env: dict) -> None:
    client: httpx.AsyncClient = live_env["client"]

    health = await client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    worker = await client.get("/api/worker/status")
    assert worker.status_code == 200
    wdata = worker.json()
    assert wdata["liveness_status"] == "HEALTHY"
    assert wdata["is_fresh"] is True
    assert wdata["total_heartbeats_received"] >= 1

    sys_metrics = await client.get("/api/system/metrics")
    assert sys_metrics.status_code == 200
    assert sys_metrics.json()["scope"] == "local_host"


async def test_upload_verify_list_and_download_flow(live_env: dict) -> None:
    client: httpx.AsyncClient = live_env["client"]
    payload = b"Hello SyncPulse from iPhone Safari! " * 500
    expected_sha = hashlib.sha256(payload).hexdigest()

    upload_resp = await client.post(
        "/api/transfers",
        files={"file": ("holiday_photo.jpg", payload, "image/jpeg")},
    )
    assert upload_resp.status_code == 202
    created = upload_resp.json()
    tid = created["id"]
    assert created["original_sha256"] == expected_sha

    done = await _wait_for_transfer_status(client, tid, {"VERIFIED", "FAILED"})
    assert done["status"] == "VERIFIED"
    assert done["stored_sha256"] == expected_sha
    assert done["can_download"] is True
    assert done["can_retry"] is False
    assert done["transfer_duration_ms"] >= 0

    # Download and verify exact bytes match original
    dl_resp = await client.get(f"/api/transfers/{tid}/download")
    assert dl_resp.status_code == 200
    assert dl_resp.content == payload

    # Summary & events reflect verified transfer
    summary = (await client.get("/api/metrics/summary")).json()
    assert summary["verified_backups"] == 1
    assert summary["failed_backups"] == 0
    assert summary["transfer_success_rate"] == 100.0

    events = (await client.get("/api/events")).json()
    event_types = [e["event_type"] for e in events]
    assert "WORKER_HEALTHY" in event_types
    assert "TRANSFER_VERIFIED" in event_types


async def test_upload_exceeding_max_size_returns_413(live_env: dict) -> None:
    client: httpx.AsyncClient = live_env["client"]
    oversize = b"Z" * (256 * 1024 + 10)

    resp = await client.post(
        "/api/transfers",
        files={"file": ("too_big.bin", oversize, "application/octet-stream")},
    )
    assert resp.status_code == 413


async def test_nonexistent_and_invalid_transfer_id_return_404(live_env: dict) -> None:
    client: httpx.AsyncClient = live_env["client"]
    assert (await client.get("/api/transfers/not-a-uuid")).status_code == 404
    assert (await client.get("/api/transfers/00000000-0000-0000-0000-000000000000")).status_code == 404
    assert (await client.get("/api/transfers/00000000-0000-0000-0000-000000000000/download")).status_code == 404
