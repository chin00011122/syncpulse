"""Unit tests for SyncPulse TCP framing and metadata validation."""

from __future__ import annotations

import asyncio
import hashlib
import struct
import uuid

import pytest

from backend.networking.protocol import (
    PROTOCOL_VERSION,
    ProtocolError,
    TransferAck,
    TransferMetadata,
    encode_json_frame,
    read_exact,
    read_json_frame,
    validate_sha256,
    validate_transfer_id,
)


def test_validate_transfer_id_accepts_valid_uuid() -> None:
    raw = str(uuid.uuid4())
    assert validate_transfer_id(raw) == raw


@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "not-a-uuid",
        "../../../etc/passwd",
        "..\\windows\\system32",
        "550e8400-e29b-41d4-a716-446655440000/extra",
        12345,
    ],
)
def test_validate_transfer_id_rejects_invalid_and_traversal(bad_id: object) -> None:
    with pytest.raises(ProtocolError) as exc_info:
        validate_transfer_id(bad_id)
    assert exc_info.value.error_code == "INVALID_TRANSFER_ID"


def test_validate_sha256() -> None:
    digest = hashlib.sha256(b"hello").hexdigest()
    assert validate_sha256(digest.upper()) == digest
    with pytest.raises(ProtocolError):
        validate_sha256("abc123")


def test_transfer_metadata_validation() -> None:
    tid = str(uuid.uuid4())
    digest = hashlib.sha256(b"test").hexdigest()
    meta = TransferMetadata.from_dict(
        {
            "version": PROTOCOL_VERSION,
            "transfer_id": tid,
            "original_filename": "photo.jpg",
            "byte_size": 4,
            "sha256": digest,
        },
        max_file_size=1024,
    )
    assert meta.transfer_id == tid
    assert meta.byte_size == 4

    with pytest.raises(ProtocolError) as exc_ver:
        TransferMetadata.from_dict(
            {
                "version": 99,
                "transfer_id": tid,
                "original_filename": "photo.jpg",
                "byte_size": 4,
                "sha256": digest,
            }
        )
    assert exc_ver.value.error_code == "UNSUPPORTED_VERSION"

    with pytest.raises(ProtocolError) as exc_size:
        TransferMetadata.from_dict(
            {
                "version": PROTOCOL_VERSION,
                "transfer_id": tid,
                "original_filename": "big.bin",
                "byte_size": 2048,
                "sha256": digest,
            },
            max_file_size=1024,
        )
    assert exc_size.value.error_code == "FILE_TOO_LARGE"


async def test_read_exact_and_json_frame_handle_fragmented_stream() -> None:
    payload = {"version": 1, "hello": "world", "n": 42}
    framed = encode_json_frame(payload)

    reader = asyncio.StreamReader()
    # Feed 1 byte at a time to simulate extreme TCP fragmentation
    for byte in framed:
        reader.feed_data(bytes([byte]))
    reader.feed_eof()

    decoded = await read_json_frame(reader, max_bytes=1024, timeout=2.0)
    assert decoded == payload


async def test_read_json_frame_rejects_oversized_length() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(struct.pack("!I", 999999))
    reader.feed_eof()

    with pytest.raises(ProtocolError) as exc_info:
        await read_json_frame(reader, max_bytes=1024, timeout=2.0)
    assert exc_info.value.error_code == "INVALID_METADATA_LENGTH"


async def test_read_exact_raises_on_incomplete_stream() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b"abc")
    reader.feed_eof()

    with pytest.raises(ProtocolError) as exc_info:
        await read_exact(reader, 10, timeout=2.0)
    assert exc_info.value.error_code == "INCOMPLETE_TRANSFER"


def test_ack_roundtrip() -> None:
    ack = TransferAck(
        version=PROTOCOL_VERSION,
        transfer_id=str(uuid.uuid4()),
        status="OK",
        bytes_written=100,
        stored_sha256="a" * 64,
        error_code=None,
        message="ok",
    )
    parsed = TransferAck.from_dict(ack.to_dict())
    assert parsed == ack
