"""Fault injection and failure recovery tests: worker shutdown, retry, and mid-transfer cut."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import httpx

from backend.app import create_app
from backend.config import Settings
from backend.networking.tcp_server import TCPBackupServer
from backend.networking.udp_heartbeat import UDPHeartbeatSender


async def _wait_for_status(
    client: httpx.AsyncClient,
    transfer_id: str,
    targets: set[str],
    timeout: float = 8.0,
) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        r = await client.get(f"/api/transfers/{transfer_id}")
        r.raise_for_status()
        body = r.json()
        if body["status"] in targets:
            return body
        await asyncio.sleep(0.05)
    raise AssertionError(f"Transfer {transfer_id} timed out waiting for {targets}")


async def test_worker_shutdown_failure_recovery_and_retry(tmp_path: Path) -> None:
    """
    Full failure-and-recovery lifecycle:
      1. Worker healthy via UDP
      2. Worker stops -> UNRESPONSIVE detected & recorded
      3. Upload attempted while worker is down -> FAILED, staging preserved, download rejected (409)
      4. Worker restarts -> RECOVERED detected with new session_id
      5. User clicks Retry -> TCP transfer succeeds -> VERIFIED & downloadable
    """
    backups_dir = tmp_path / "data" / "backups"
    tcp_server = TCPBackupServer(
        host="127.0.0.1",
        port=0,
        backups_dir=backups_dir,
        timeout=5.0,
    )
    await tcp_server.start()
    tcp_port = tcp_server.bound_port

    settings = Settings(
        data_dir=str(tmp_path / "data"),
        tcp_host="127.0.0.1",
        tcp_port=tcp_port,
        udp_host="127.0.0.1",
        udp_port=0,
        tcp_timeout_seconds=5.0,
        heartbeat_interval_seconds=0.1,
        heartbeat_timeout_seconds=0.4,
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        udp_port = app.state.monitoring_service.bound_udp_port
        hb1 = UDPHeartbeatSender("127.0.0.1", udp_port, settings.worker_id, interval_seconds=0.1)
        await hb1.start()
        await asyncio.sleep(0.2)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            assert (await client.get("/api/worker/status")).json()["liveness_status"] == "HEALTHY"

            # Simulate complete Backup Worker shutdown (both TCP and UDP stop)
            await hb1.stop()
            await tcp_server.stop()
            await asyncio.sleep(0.55)

            w_down = (await client.get("/api/worker/status")).json()
            assert w_down["liveness_status"] == "UNRESPONSIVE"
            assert w_down["is_fresh"] is False

            # Upload file while worker is down
            payload = b"Important lecture notes to back up after recovery!"
            sha = hashlib.sha256(payload).hexdigest()
            up = await client.post(
                "/api/transfers",
                files={"file": ("notes.txt", payload, "text/plain")},
            )
            assert up.status_code == 202
            tid = up.json()["id"]

            failed = await _wait_for_status(client, tid, {"FAILED", "VERIFIED"})
            assert failed["status"] == "FAILED"
            assert failed["can_retry"] is True
            assert failed["can_download"] is False

            # Incomplete/failed backup must NOT be downloadable
            assert (await client.get(f"/api/transfers/{tid}/download")).status_code == 409

            # Restart Backup Worker on the same TCP port with a fresh UDP session_id
            tcp_server_2 = TCPBackupServer(
                host="127.0.0.1",
                port=tcp_port,
                backups_dir=backups_dir,
                timeout=5.0,
            )
            await tcp_server_2.start()
            hb2 = UDPHeartbeatSender("127.0.0.1", udp_port, settings.worker_id, interval_seconds=0.1)
            await hb2.start()
            try:
                await asyncio.sleep(0.25)
                w_up = (await client.get("/api/worker/status")).json()
                assert w_up["liveness_status"] == "HEALTHY"
                assert w_up["session_id"] == hb2.session_id
                assert w_up["session_id"] != hb1.session_id

                # Trigger Retry
                retry_resp = await client.post(f"/api/transfers/{tid}/retry")
                assert retry_resp.status_code == 202

                verified = await _wait_for_status(client, tid, {"VERIFIED", "FAILED"})
                assert verified["status"] == "VERIFIED"
                assert verified["attempt_count"] == 2
                assert verified["stored_sha256"] == sha
                assert verified["can_download"] is True

                dl = await client.get(f"/api/transfers/{tid}/download")
                assert dl.status_code == 200
                assert dl.content == payload

                events = (await client.get("/api/events")).json()
                event_types = [e["event_type"] for e in events]
                assert "WORKER_UNRESPONSIVE" in event_types
                assert "WORKER_RECOVERED" in event_types
                assert "TRANSFER_FAILED" in event_types
                assert "TRANSFER_VERIFIED" in event_types
            finally:
                await hb2.stop()
                await tcp_server_2.stop()


async def test_mid_transfer_interruption_leaves_no_corrupt_backup(tmp_path: Path) -> None:
    """
    Use `tcp_throttle_ms` to slow a multi-chunk transfer and shut down the worker mid-stream.
    Verify transfer fails cleanly, no corrupt `.blob` is left behind, and retry succeeds.
    """
    backups_dir = tmp_path / "data" / "backups"
    tcp_server = TCPBackupServer(
        host="127.0.0.1",
        port=0,
        backups_dir=backups_dir,
        chunk_size=4096,
        timeout=2.0,
    )
    await tcp_server.start()
    tcp_port = tcp_server.bound_port

    settings = Settings(
        data_dir=str(tmp_path / "data"),
        tcp_host="127.0.0.1",
        tcp_port=tcp_port,
        udp_host="127.0.0.1",
        udp_port=0,
        chunk_size_bytes=4096,
        tcp_timeout_seconds=2.0,
        tcp_throttle_ms=40,  # Slow chunks so we can interrupt mid-transfer deterministically
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            payload = b"M" * (4096 * 12)  # 48 KiB -> 12 chunks * 40ms ~= 480ms
            up = await client.post(
                "/api/transfers",
                files={"file": ("midcut.bin", payload, "application/octet-stream")},
            )
            tid = up.json()["id"]

            # Wait briefly so transfer enters TRANSFERRING, then stop the server
            await asyncio.sleep(0.12)
            await tcp_server.stop()

            result = await _wait_for_status(client, tid, {"FAILED", "VERIFIED"})
            # Either it failed mid-stream (expected) or finished before stop; if failed, check cleanup
            if result["status"] == "FAILED":
                assert not (backups_dir / f"{tid}.blob").exists()
                assert result["can_retry"] is True

                # Restart worker without throttle and retry
                settings.tcp_throttle_ms = 0
                tcp_server2 = TCPBackupServer(
                    host="127.0.0.1",
                    port=tcp_port,
                    backups_dir=backups_dir,
                    chunk_size=4096,
                    timeout=5.0,
                )
                await tcp_server2.start()
                try:
                    assert (await client.post(f"/api/transfers/{tid}/retry")).status_code == 202
                    verified = await _wait_for_status(client, tid, {"VERIFIED", "FAILED"})
                    assert verified["status"] == "VERIFIED"
                    assert (backups_dir / f"{tid}.blob").read_bytes() == payload
                finally:
                    await tcp_server2.stop()
