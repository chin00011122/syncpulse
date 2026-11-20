"""Standalone Backup Worker process (Process B): TCP Backup Server + UDP Heartbeat Sender."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from backend.config import get_settings
from backend.networking.tcp_server import TCPBackupServer
from backend.networking.udp_heartbeat import UDPHeartbeatSender

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("syncpulse.worker")


async def run_worker() -> None:
    settings = get_settings()
    settings.ensure_directories()

    tcp_server = TCPBackupServer(
        host=settings.tcp_host,
        port=settings.tcp_port,
        backups_dir=settings.backups_dir,
        max_file_size=settings.max_file_size_bytes,
        max_metadata_bytes=settings.max_metadata_bytes,
        chunk_size=settings.chunk_size_bytes,
        timeout=settings.tcp_timeout_seconds,
    )
    heartbeat_sender = UDPHeartbeatSender(
        target_host=settings.udp_host,
        target_port=settings.udp_port,
        worker_id=settings.worker_id,
        interval_seconds=settings.heartbeat_interval_seconds,
        drop_every_n=settings.udp_drop_every_n,
    )

    stop_event = asyncio.Event()

    def _request_stop() -> None:
        logger.info("Shutdown signal received; stopping Backup Worker...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Windows event loops do not support add_signal_handler; KeyboardInterrupt handles Ctrl+C
            pass

    await tcp_server.start()
    await heartbeat_sender.start()
    logger.info(
        "SyncPulse Backup Worker ready (worker_id=%s, session_id=%s, TCP=%s:%d, UDP->%s:%d)",
        settings.worker_id,
        heartbeat_sender.session_id,
        settings.tcp_host,
        tcp_server.bound_port,
        settings.udp_host,
        settings.udp_port,
    )

    try:
        await stop_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await heartbeat_sender.stop()
        await tcp_server.stop()
        logger.info("SyncPulse Backup Worker exited cleanly")


def main() -> None:
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
