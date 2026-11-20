"""Asyncio TCP Backup Server running inside the independent Backup Worker process."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path

from backend.networking.protocol import (
    DEFAULT_CHUNK_SIZE_BYTES,
    DEFAULT_MAX_FILE_SIZE_BYTES,
    DEFAULT_MAX_METADATA_BYTES,
    PROTOCOL_VERSION,
    ProtocolError,
    TransferAck,
    TransferMetadata,
    encode_json_frame,
    read_json_frame,
)

logger = logging.getLogger("syncpulse.tcp_server")


def compute_file_sha256(path: Path, chunk_size: int = DEFAULT_CHUNK_SIZE_BYTES) -> tuple[int, str]:
    """Compute exact byte size and SHA-256 hex digest of an existing file on disk."""
    hasher = hashlib.sha256()
    total = 0
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            total += len(chunk)
            hasher.update(chunk)
    return total, hasher.hexdigest()


class TCPBackupServer:
    """
    Receives framed binary file transfers over raw TCP sockets.

    Wire flow per connection:
      1. Read 4-byte length + UTF-8 JSON TransferMetadata
      2. Validate protocol version, UUID transfer_id, file size, sha256
      3. Check idempotency if `<transfer_id>.blob` already exists
      4. Stream exact `byte_size` bytes into `<transfer_id>.part` while hashing SHA-256
      5. Verify byte count and SHA-256 match metadata
      6. Atomically commit `<transfer_id>.part` -> `<transfer_id>.blob` via os.replace()
      7. Send 4-byte length + UTF-8 JSON TransferAck
    """

    def __init__(
        self,
        host: str,
        port: int,
        backups_dir: Path,
        max_file_size: int = DEFAULT_MAX_FILE_SIZE_BYTES,
        max_metadata_bytes: int = DEFAULT_MAX_METADATA_BYTES,
        chunk_size: int = DEFAULT_CHUNK_SIZE_BYTES,
        timeout: float = 30.0,
    ) -> None:
        self.host = host
        self.port = port
        self.backups_dir = Path(backups_dir)
        self.max_file_size = max_file_size
        self.max_metadata_bytes = max_metadata_bytes
        self.chunk_size = chunk_size
        self.timeout = timeout
        self._server: asyncio.AbstractServer | None = None
        self._active_transfers: set[str] = set()

    @property
    def bound_port(self) -> int:
        if self._server and self._server.sockets:
            return int(self._server.sockets[0].getsockname()[1])
        return self.port

    def cleanup_stale_partials(self) -> int:
        """Remove any leftover `.part` files from an earlier interrupted run."""
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        removed = 0
        for part_file in self.backups_dir.glob("*.part"):
            try:
                part_file.unlink()
                removed += 1
            except OSError as exc:
                logger.warning("Failed to remove stale partial %s: %s", part_file, exc)
        return removed

    async def start(self) -> None:
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        stale = self.cleanup_stale_partials()
        if stale:
            logger.info("Cleaned up %d stale .part file(s) on startup", stale)
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.host,
            port=self.port,
        )
        logger.info("TCP Backup Server listening on %s:%d", self.host, self.bound_port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            logger.info("TCP Backup Server stopped")

    async def _send_ack(self, writer: asyncio.StreamWriter, ack: TransferAck) -> None:
        try:
            writer.write(encode_json_frame(ack.to_dict()))
            await asyncio.wait_for(writer.drain(), timeout=self.timeout)
        except Exception as exc:
            logger.warning("Failed to send ACK for transfer %s: %s", ack.transfer_id, exc)

    async def _drain_stream(self, reader: asyncio.StreamReader, remaining: int) -> None:
        """Drain expected payload bytes so the client does not hit a reset before reading ACK."""
        while remaining > 0:
            to_read = min(self.chunk_size, remaining)
            chunk = await asyncio.wait_for(reader.read(to_read), timeout=self.timeout)
            if not chunk:
                raise ProtocolError(
                    "INCOMPLETE_TRANSFER",
                    f"Connection closed with {remaining} bytes remaining during drain",
                )
            remaining -= len(chunk)

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername")
        transfer_id: str | None = None
        part_path: Path | None = None
        registered_active = False

        try:
            raw_meta = await read_json_frame(
                reader,
                max_bytes=self.max_metadata_bytes,
                timeout=self.timeout,
            )
            meta = TransferMetadata.from_dict(raw_meta, max_file_size=self.max_file_size)
            transfer_id = meta.transfer_id

            if transfer_id in self._active_transfers:
                raise ProtocolError(
                    "DUPLICATE_IN_PROGRESS",
                    f"Transfer {transfer_id} is already in progress",
                )
            self._active_transfers.add(transfer_id)
            registered_active = True

            final_path = self.backups_dir / f"{transfer_id}.blob"
            part_path = self.backups_dir / f"{transfer_id}.part"

            # Idempotency check: if final .blob already exists, verify against disk truth
            if final_path.exists():
                existing_size, existing_sha = compute_file_sha256(final_path, self.chunk_size)
                if existing_size == meta.byte_size and existing_sha == meta.sha256:
                    # Drain incoming stream cleanly so the client can read the OK ACK without EPIPE
                    await self._drain_stream(reader, meta.byte_size)
                    logger.info(
                        "Idempotent hit for transfer %s from %s (%d bytes)",
                        transfer_id,
                        peer,
                        existing_size,
                    )
                    await self._send_ack(
                        writer,
                        TransferAck(
                            version=PROTOCOL_VERSION,
                            transfer_id=transfer_id,
                            status="OK",
                            bytes_written=existing_size,
                            stored_sha256=existing_sha,
                            error_code=None,
                            message="Already verified (idempotent)",
                        ),
                    )
                    return

                raise ProtocolError(
                    "DUPLICATE_CONFLICT",
                    f"Transfer {transfer_id} already exists with different size or checksum",
                )

            # Stream exact byte_size into temporary .part file while computing SHA-256
            hasher = hashlib.sha256()
            bytes_received = 0
            remaining = meta.byte_size

            try:
                with part_path.open("wb") as out_file:
                    while remaining > 0:
                        to_read = min(self.chunk_size, remaining)
                        try:
                            chunk = await asyncio.wait_for(
                                reader.read(to_read),
                                timeout=self.timeout,
                            )
                        except asyncio.TimeoutError as exc:
                            raise ProtocolError(
                                "TIMEOUT",
                                f"Timed out receiving payload after {bytes_received}/{meta.byte_size} bytes",
                            ) from exc

                        if not chunk:
                            raise ProtocolError(
                                "INCOMPLETE_TRANSFER",
                                f"Connection closed after {bytes_received}/{meta.byte_size} bytes",
                            )

                        out_file.write(chunk)
                        hasher.update(chunk)
                        bytes_received += len(chunk)
                        remaining -= len(chunk)
                    out_file.flush()
                    os.fsync(out_file.fileno())
            except OSError as exc:
                raise ProtocolError("STORAGE_ERROR", f"Disk write error: {exc}") from exc

            computed_sha = hasher.hexdigest()
            if bytes_received != meta.byte_size:
                raise ProtocolError(
                    "INCOMPLETE_TRANSFER",
                    f"Byte count mismatch: expected {meta.byte_size}, got {bytes_received}",
                )

            if computed_sha != meta.sha256:
                raise ProtocolError(
                    "CHECKSUM_MISMATCH",
                    f"SHA-256 mismatch: expected {meta.sha256}, computed {computed_sha}",
                )

            # Atomically move verified .part file to final .blob
            try:
                os.replace(part_path, final_path)
                part_path = None  # Committed; do not delete in cleanup
            except OSError as exc:
                raise ProtocolError("STORAGE_ERROR", f"Atomic commit failed: {exc}") from exc

            logger.info(
                "Verified and stored transfer %s (%d bytes, sha256=%s...) from %s",
                transfer_id,
                bytes_received,
                computed_sha[:12],
                peer,
            )
            await self._send_ack(
                writer,
                TransferAck(
                    version=PROTOCOL_VERSION,
                    transfer_id=transfer_id,
                    status="OK",
                    bytes_written=bytes_received,
                    stored_sha256=computed_sha,
                    error_code=None,
                    message="Transfer verified and committed",
                ),
            )

        except ProtocolError as exc:
            logger.warning(
                "TCP transfer error (transfer_id=%s, code=%s): %s",
                transfer_id,
                exc.error_code,
                exc.message,
            )
            await self._send_ack(
                writer,
                TransferAck(
                    version=PROTOCOL_VERSION,
                    transfer_id=transfer_id,
                    status="ERROR",
                    bytes_written=0,
                    stored_sha256=None,
                    error_code=exc.error_code,
                    message=exc.message,
                ),
            )
        except Exception as exc:
            logger.exception("Unexpected error handling TCP client %s", peer)
            await self._send_ack(
                writer,
                TransferAck(
                    version=PROTOCOL_VERSION,
                    transfer_id=transfer_id,
                    status="ERROR",
                    bytes_written=0,
                    stored_sha256=None,
                    error_code="INTERNAL_ERROR",
                    message=f"Unexpected server error: {exc}",
                ),
            )
        finally:
            if registered_active and transfer_id is not None:
                self._active_transfers.discard(transfer_id)
            if part_path is not None and part_path.exists():
                try:
                    part_path.unlink()
                except OSError:
                    pass
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
