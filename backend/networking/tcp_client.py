"""Asyncio TCP Backup Client used by the API Server to stream staged files to the Backup Worker."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from backend.networking.protocol import (
    DEFAULT_CHUNK_SIZE_BYTES,
    DEFAULT_MAX_METADATA_BYTES,
    PROTOCOL_VERSION,
    ProtocolError,
    TransferAck,
    TransferMetadata,
    encode_json_frame,
    read_json_frame,
)

logger = logging.getLogger("syncpulse.tcp_client")

ProgressCallback = Callable[[int, int], Awaitable[None] | None]


@dataclass(slots=True)
class TCPTransferResult:
    success: bool
    ack: TransferAck | None
    bytes_sent: int
    tcp_connect_ms: float
    transfer_duration_ms: float
    error_code: str | None = None
    error_message: str | None = None


async def send_file_over_tcp(
    host: str,
    port: int,
    transfer_id: str,
    original_filename: str,
    file_path: Path,
    expected_sha256: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE_BYTES,
    timeout: float = 30.0,
    throttle_ms: int = 0,
    on_progress: ProgressCallback | None = None,
) -> TCPTransferResult:
    """
    Connect to the Backup Worker over TCP, stream metadata + file bytes, and await ACK.

    `throttle_ms` (default 0) adds a small per-chunk delay only when explicitly enabled
    for mid-transfer interruption demos/tests.
    """
    if not file_path.exists():
        return TCPTransferResult(
            success=False,
            ack=None,
            bytes_sent=0,
            tcp_connect_ms=0.0,
            transfer_duration_ms=0.0,
            error_code="STAGING_FILE_MISSING",
            error_message=f"Staged file not found: {file_path.name}",
        )

    byte_size = file_path.stat().st_size
    connect_start = time.perf_counter()

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return TCPTransferResult(
            success=False,
            ack=None,
            bytes_sent=0,
            tcp_connect_ms=(time.perf_counter() - connect_start) * 1000.0,
            transfer_duration_ms=0.0,
            error_code="CONNECTION_TIMEOUT",
            error_message=f"Timed out connecting to Backup Worker at {host}:{port}",
        )
    except OSError as exc:
        return TCPTransferResult(
            success=False,
            ack=None,
            bytes_sent=0,
            tcp_connect_ms=(time.perf_counter() - connect_start) * 1000.0,
            transfer_duration_ms=0.0,
            error_code="WORKER_UNREACHABLE",
            error_message=f"Could not connect to Backup Worker at {host}:{port}: {exc}",
        )

    tcp_connect_ms = (time.perf_counter() - connect_start) * 1000.0
    transfer_start = time.perf_counter()
    bytes_sent = 0

    try:
        metadata = TransferMetadata(
            version=PROTOCOL_VERSION,
            transfer_id=transfer_id,
            original_filename=original_filename,
            byte_size=byte_size,
            sha256=expected_sha256,
        )
        writer.write(encode_json_frame(metadata.to_dict()))
        await asyncio.wait_for(writer.drain(), timeout=timeout)

        write_error: OSError | None = None
        with file_path.open("rb") as src:
            while True:
                chunk = src.read(chunk_size)
                if not chunk:
                    break
                try:
                    writer.write(chunk)
                    await asyncio.wait_for(writer.drain(), timeout=timeout)
                except OSError as exc:
                    # Server may have rejected metadata and sent an early error ACK before closing
                    write_error = exc
                    break
                bytes_sent += len(chunk)
                if on_progress is not None:
                    maybe_coro = on_progress(bytes_sent, byte_size)
                    if asyncio.iscoroutine(maybe_coro):
                        await maybe_coro
                if throttle_ms > 0:
                    await asyncio.sleep(throttle_ms / 1000.0)

        try:
            raw_ack = await read_json_frame(
                reader,
                max_bytes=DEFAULT_MAX_METADATA_BYTES,
                timeout=timeout,
            )
            ack = TransferAck.from_dict(raw_ack)
        except ProtocolError as exc:
            duration_ms = (time.perf_counter() - transfer_start) * 1000.0
            if write_error is not None:
                return TCPTransferResult(
                    success=False,
                    ack=None,
                    bytes_sent=bytes_sent,
                    tcp_connect_ms=tcp_connect_ms,
                    transfer_duration_ms=duration_ms,
                    error_code="CONNECTION_LOST",
                    error_message=f"Connection lost during transfer: {write_error}",
                )
            return TCPTransferResult(
                success=False,
                ack=None,
                bytes_sent=bytes_sent,
                tcp_connect_ms=tcp_connect_ms,
                transfer_duration_ms=duration_ms,
                error_code=exc.error_code,
                error_message=exc.message,
            )

        duration_ms = (time.perf_counter() - transfer_start) * 1000.0

        if ack.status == "OK" and ack.stored_sha256 == expected_sha256 and ack.bytes_written == byte_size:
            return TCPTransferResult(
                success=True,
                ack=ack,
                bytes_sent=bytes_sent,
                tcp_connect_ms=tcp_connect_ms,
                transfer_duration_ms=duration_ms,
            )

        return TCPTransferResult(
            success=False,
            ack=ack,
            bytes_sent=bytes_sent,
            tcp_connect_ms=tcp_connect_ms,
            transfer_duration_ms=duration_ms,
            error_code=ack.error_code or "VERIFICATION_FAILED",
            error_message=ack.message or "Worker ACK did not verify transfer",
        )
    except asyncio.TimeoutError:
        duration_ms = (time.perf_counter() - transfer_start) * 1000.0
        return TCPTransferResult(
            success=False,
            ack=None,
            bytes_sent=bytes_sent,
            tcp_connect_ms=tcp_connect_ms,
            transfer_duration_ms=duration_ms,
            error_code="TIMEOUT",
            error_message="Timed out during TCP file transfer",
        )
    except OSError as exc:
        duration_ms = (time.perf_counter() - transfer_start) * 1000.0
        return TCPTransferResult(
            success=False,
            ack=None,
            bytes_sent=bytes_sent,
            tcp_connect_ms=tcp_connect_ms,
            transfer_duration_ms=duration_ms,
            error_code="CONNECTION_LOST",
            error_message=f"TCP socket error: {exc}",
        )
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
