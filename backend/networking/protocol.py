"""Custom length-prefixed framing protocol for SyncPulse TCP file transfers."""

from __future__ import annotations

import asyncio
import json
import re
import struct
import uuid
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 1
LENGTH_PREFIX_FORMAT = "!I"  # 4-byte unsigned big-endian integer
LENGTH_PREFIX_SIZE = struct.calcsize(LENGTH_PREFIX_FORMAT)

DEFAULT_MAX_METADATA_BYTES = 16 * 1024  # 16 KiB
DEFAULT_MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MiB
DEFAULT_CHUNK_SIZE_BYTES = 64 * 1024  # 64 KiB

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ProtocolError(Exception):
    """Structured error raised when TCP framing or metadata validation fails."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


def validate_transfer_id(value: Any) -> str:
    """Validate that transfer_id is a canonical UUID string (blocks path traversal)."""
    if not isinstance(value, str) or not value:
        raise ProtocolError("INVALID_TRANSFER_ID", "transfer_id must be a non-empty UUID string")
    if "/" in value or "\\" in value or ".." in value:
        raise ProtocolError("INVALID_TRANSFER_ID", "transfer_id contains illegal path characters")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise ProtocolError("INVALID_TRANSFER_ID", f"Invalid UUID format: {value!r}") from exc
    return str(parsed)


def validate_sha256(value: Any) -> str:
    """Validate a 64-character hexadecimal SHA-256 digest."""
    if not isinstance(value, str):
        raise ProtocolError("INVALID_METADATA", "sha256 must be a string")
    normalized = value.lower()
    if not _SHA256_RE.match(normalized):
        raise ProtocolError("INVALID_METADATA", "sha256 must be a 64-character hex digest")
    return normalized


@dataclass(slots=True)
class TransferMetadata:
    version: int
    transfer_id: str
    original_filename: str
    byte_size: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "transfer_id": self.transfer_id,
            "original_filename": self.original_filename,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(
        cls,
        data: Any,
        max_file_size: int = DEFAULT_MAX_FILE_SIZE_BYTES,
    ) -> TransferMetadata:
        if not isinstance(data, dict):
            raise ProtocolError("INVALID_METADATA", "Metadata payload must be a JSON object")

        version = data.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version != PROTOCOL_VERSION:
            raise ProtocolError(
                "UNSUPPORTED_VERSION",
                f"Unsupported protocol version: {version!r} (expected {PROTOCOL_VERSION})",
            )

        transfer_id = validate_transfer_id(data.get("transfer_id"))

        original_filename = data.get("original_filename")
        if not isinstance(original_filename, str) or not original_filename.strip():
            raise ProtocolError("INVALID_METADATA", "original_filename must be a non-empty string")
        if len(original_filename) > 255:
            raise ProtocolError("INVALID_METADATA", "original_filename exceeds 255 characters")

        byte_size = data.get("byte_size")
        if not isinstance(byte_size, int) or isinstance(byte_size, bool) or byte_size < 0:
            raise ProtocolError("INVALID_METADATA", "byte_size must be a non-negative integer")
        if byte_size > max_file_size:
            raise ProtocolError(
                "FILE_TOO_LARGE",
                f"File size {byte_size} exceeds maximum allowed {max_file_size} bytes",
            )

        sha256 = validate_sha256(data.get("sha256"))

        return cls(
            version=version,
            transfer_id=transfer_id,
            original_filename=original_filename,
            byte_size=byte_size,
            sha256=sha256,
        )


@dataclass(slots=True)
class TransferAck:
    version: int
    transfer_id: str | None
    status: str  # "OK" or "ERROR"
    bytes_written: int
    stored_sha256: str | None
    error_code: str | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "transfer_id": self.transfer_id,
            "status": self.status,
            "bytes_written": self.bytes_written,
            "stored_sha256": self.stored_sha256,
            "error_code": self.error_code,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: Any) -> TransferAck:
        if not isinstance(data, dict):
            raise ProtocolError("INVALID_ACK", "ACK payload must be a JSON object")
        status = data.get("status")
        if status not in ("OK", "ERROR"):
            raise ProtocolError("INVALID_ACK", f"Invalid ACK status: {status!r}")
        return cls(
            version=int(data.get("version", PROTOCOL_VERSION)),
            transfer_id=data.get("transfer_id"),
            status=status,
            bytes_written=int(data.get("bytes_written", 0)),
            stored_sha256=data.get("stored_sha256"),
            error_code=data.get("error_code"),
            message=str(data.get("message", "")),
        )


def encode_json_frame(payload: dict[str, Any]) -> bytes:
    """Encode a JSON object with a 4-byte big-endian length prefix."""
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    header = struct.pack(LENGTH_PREFIX_FORMAT, len(body))
    return header + body


async def read_exact(
    reader: asyncio.StreamReader,
    num_bytes: int,
    timeout: float,
) -> bytes:
    """
    Read exactly `num_bytes` from a TCP StreamReader.

    TCP is a byte-stream protocol where a single recv() may return fewer bytes
    than requested. StreamReader.readexactly() loops until the exact count
    arrives or EOF is reached.
    """
    if num_bytes == 0:
        return b""
    try:
        return await asyncio.wait_for(reader.readexactly(num_bytes), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise ProtocolError("TIMEOUT", f"Timed out waiting for {num_bytes} bytes") from exc
    except asyncio.IncompleteReadError as exc:
        raise ProtocolError(
            "INCOMPLETE_TRANSFER",
            f"Connection closed early after {len(exc.partial)} of {num_bytes} bytes",
        ) from exc


async def read_json_frame(
    reader: asyncio.StreamReader,
    max_bytes: int = DEFAULT_MAX_METADATA_BYTES,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Read a length-prefixed UTF-8 JSON frame from a TCP stream."""
    header = await read_exact(reader, LENGTH_PREFIX_SIZE, timeout=timeout)
    (length,) = struct.unpack(LENGTH_PREFIX_FORMAT, header)
    if length == 0 or length > max_bytes:
        raise ProtocolError(
            "INVALID_METADATA_LENGTH",
            f"Frame length {length} outside allowed range 1..{max_bytes}",
        )
    raw = await read_exact(reader, length, timeout=timeout)
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("INVALID_JSON", f"Malformed JSON frame: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ProtocolError("INVALID_JSON", "JSON frame root must be an object")
    return parsed
