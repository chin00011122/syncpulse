"""Integration tests for real TCP socket file transfers between client and worker server."""

from __future__ import annotations

import asyncio
import hashlib
import os
import struct
import uuid
from pathlib import Path

import pytest

from backend.networking.protocol import (
    PROTOCOL_VERSION,
    TransferAck,
    encode_json_frame,
    read_json_frame,
)
from backend.networking.tcp_client import send_file_over_tcp
from backend.networking.tcp_server import TCPBackupServer


@pytest.fixture
async def tcp_server(tmp_path: Path):
    backups_dir = tmp_path / "backups"
    server = TCPBackupServer(
        host="127.0.0.1",
        port=0,  # OS assigns an available ephemeral port
        backups_dir=backups_dir,
        max_file_size=1024 * 1024,  # 1 MiB for tests
        chunk_size=16 * 1024,
        timeout=5.0,
    )
    await server.start()
    yield server
    await server.stop()


def _write_temp_file(path: Path, data: bytes) -> str:
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


async def test_small_file_transfer(tcp_server: TCPBackupServer, tmp_path: Path) -> None:
    data = b"SyncPulse small file payload"
    src = tmp_path / "small.txt"
    sha = _write_temp_file(src, data)
    tid = str(uuid.uuid4())

    result = await send_file_over_tcp(
        host="127.0.0.1",
        port=tcp_server.bound_port,
        transfer_id=tid,
        original_filename="small.txt",
        file_path=src,
        expected_sha256=sha,
    )

    assert result.success is True
    assert result.ack is not None
    assert result.ack.status == "OK"
    assert result.ack.stored_sha256 == sha
    assert result.ack.bytes_written == len(data)
    stored = tcp_server.backups_dir / f"{tid}.blob"
    assert stored.exists()
    assert stored.read_bytes() == data


async def test_multi_chunk_file_transfer(tcp_server: TCPBackupServer, tmp_path: Path) -> None:
    data = os.urandom(300 * 1024)  # 300 KiB across multiple 16 KiB chunks
    src = tmp_path / "large.bin"
    sha = _write_temp_file(src, data)
    tid = str(uuid.uuid4())
    progress_updates: list[int] = []

    result = await send_file_over_tcp(
        host="127.0.0.1",
        port=tcp_server.bound_port,
        transfer_id=tid,
        original_filename="large.bin",
        file_path=src,
        expected_sha256=sha,
        chunk_size=16 * 1024,
        on_progress=lambda sent, _total: progress_updates.append(sent),
    )

    assert result.success is True
    assert progress_updates[-1] == len(data)
    assert len(progress_updates) > 10
    assert (tcp_server.backups_dir / f"{tid}.blob").read_bytes() == data


async def test_empty_file_transfer(tcp_server: TCPBackupServer, tmp_path: Path) -> None:
    src = tmp_path / "empty.txt"
    sha = _write_temp_file(src, b"")
    tid = str(uuid.uuid4())

    result = await send_file_over_tcp(
        host="127.0.0.1",
        port=tcp_server.bound_port,
        transfer_id=tid,
        original_filename="empty.txt",
        file_path=src,
        expected_sha256=sha,
    )

    assert result.success is True
    assert (tcp_server.backups_dir / f"{tid}.blob").read_bytes() == b""


async def test_file_exceeding_max_size_rejected(tcp_server: TCPBackupServer, tmp_path: Path) -> None:
    # Server max_file_size is 1 MiB in fixture
    data = b"x" * (1024 * 1024 + 1)
    src = tmp_path / "too_big.bin"
    sha = _write_temp_file(src, data)
    tid = str(uuid.uuid4())

    result = await send_file_over_tcp(
        host="127.0.0.1",
        port=tcp_server.bound_port,
        transfer_id=tid,
        original_filename="too_big.bin",
        file_path=src,
        expected_sha256=sha,
    )

    assert result.success is False
    assert result.error_code in ("FILE_TOO_LARGE", "CONNECTION_LOST")
    assert not (tcp_server.backups_dir / f"{tid}.blob").exists()


async def test_fragmented_tcp_writes_handled_cleanly(tcp_server: TCPBackupServer) -> None:
    """Send metadata and payload in tiny 7-byte TCP writes to verify partial-read framing."""
    payload = b"Fragmented TCP byte stream verification test! " * 20
    sha = hashlib.sha256(payload).hexdigest()
    tid = str(uuid.uuid4())

    frame = encode_json_frame(
        {
            "version": PROTOCOL_VERSION,
            "transfer_id": tid,
            "original_filename": "frag.txt",
            "byte_size": len(payload),
            "sha256": sha,
        }
    )
    full_wire = frame + payload

    reader, writer = await asyncio.open_connection("127.0.0.1", tcp_server.bound_port)
    try:
        for idx in range(0, len(full_wire), 7):
            writer.write(full_wire[idx : idx + 7])
            await writer.drain()
            await asyncio.sleep(0)

        raw_ack = await read_json_frame(reader, timeout=5.0)
        ack = TransferAck.from_dict(raw_ack)
        assert ack.status == "OK"
        assert ack.stored_sha256 == sha
        assert (tcp_server.backups_dir / f"{tid}.blob").read_bytes() == payload
    finally:
        writer.close()
        await writer.wait_closed()


async def test_checksum_mismatch_rejected_and_cleaned_up(
    tcp_server: TCPBackupServer, tmp_path: Path
) -> None:
    data = b"real file bytes"
    src = tmp_path / "corrupt.bin"
    src.write_bytes(data)
    wrong_sha = hashlib.sha256(b"different bytes").hexdigest()
    tid = str(uuid.uuid4())

    result = await send_file_over_tcp(
        host="127.0.0.1",
        port=tcp_server.bound_port,
        transfer_id=tid,
        original_filename="corrupt.bin",
        file_path=src,
        expected_sha256=wrong_sha,
    )

    assert result.success is False
    assert result.error_code == "CHECKSUM_MISMATCH"
    assert not (tcp_server.backups_dir / f"{tid}.blob").exists()
    assert not (tcp_server.backups_dir / f"{tid}.part").exists()


async def test_incomplete_transfer_cleans_up_part_file(tcp_server: TCPBackupServer) -> None:
    tid = str(uuid.uuid4())
    expected_payload = b"A" * 1000
    sha = hashlib.sha256(expected_payload).hexdigest()

    reader, writer = await asyncio.open_connection("127.0.0.1", tcp_server.bound_port)
    writer.write(
        encode_json_frame(
            {
                "version": PROTOCOL_VERSION,
                "transfer_id": tid,
                "original_filename": "cut.bin",
                "byte_size": len(expected_payload),
                "sha256": sha,
            }
        )
    )
    writer.write(b"A" * 200)  # Disconnect after 200 of 1000 bytes
    await writer.drain()
    writer.close()
    await writer.wait_closed()

    await asyncio.sleep(0.1)
    assert not (tcp_server.backups_dir / f"{tid}.blob").exists()
    assert not (tcp_server.backups_dir / f"{tid}.part").exists()


async def test_invalid_metadata_returns_error_ack(tcp_server: TCPBackupServer) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", tcp_server.bound_port)
    try:
        bad_json = b"{not-valid-json"
        writer.write(struct.pack("!I", len(bad_json)) + bad_json)
        await writer.drain()
        ack = TransferAck.from_dict(await read_json_frame(reader, timeout=5.0))
        assert ack.status == "ERROR"
        assert ack.error_code == "INVALID_JSON"
    finally:
        writer.close()
        await writer.wait_closed()


async def test_duplicate_transfer_id_idempotency_and_conflict(
    tcp_server: TCPBackupServer, tmp_path: Path
) -> None:
    data = b"idempotent content"
    src1 = tmp_path / "f1.txt"
    sha1 = _write_temp_file(src1, data)
    tid = str(uuid.uuid4())

    first = await send_file_over_tcp(
        host="127.0.0.1",
        port=tcp_server.bound_port,
        transfer_id=tid,
        original_filename="f1.txt",
        file_path=src1,
        expected_sha256=sha1,
    )
    assert first.success is True

    # Exact same transfer_id + same content -> idempotent OK
    second = await send_file_over_tcp(
        host="127.0.0.1",
        port=tcp_server.bound_port,
        transfer_id=tid,
        original_filename="f1.txt",
        file_path=src1,
        expected_sha256=sha1,
    )
    assert second.success is True
    assert second.ack is not None
    assert "idempotent" in second.ack.message.lower()

    # Same transfer_id + different content -> DUPLICATE_CONFLICT
    src2 = tmp_path / "f2.txt"
    sha2 = _write_temp_file(src2, b"different content!")
    conflict = await send_file_over_tcp(
        host="127.0.0.1",
        port=tcp_server.bound_port,
        transfer_id=tid,
        original_filename="f2.txt",
        file_path=src2,
        expected_sha256=sha2,
    )
    assert conflict.success is False
    assert conflict.error_code == "DUPLICATE_CONFLICT"


async def test_worker_not_running_returns_unreachable(tmp_path: Path) -> None:
    src = tmp_path / "offline.txt"
    sha = _write_temp_file(src, b"offline test")
    # Bind and immediately close a socket to pick an unused local port
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    unused_port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()

    result = await send_file_over_tcp(
        host="127.0.0.1",
        port=unused_port,
        transfer_id=str(uuid.uuid4()),
        original_filename="offline.txt",
        file_path=src,
        expected_sha256=sha,
        timeout=5.0,
    )
    assert result.success is False
    assert result.error_code == "WORKER_UNREACHABLE"
