# SyncPulse Network Protocol Design

SyncPulse implements two distinct transport protocols in the backend: a **custom framed TCP protocol** for reliable file backup and a **lightweight UDP datagram protocol** for service liveness heartbeats.

---

## 1. Custom TCP File Transfer Protocol

### 1.1 Why Framing Is Required on TCP

TCP provides a reliable, ordered **byte stream** with no built-in message boundaries. A single `send()` on the client may arrive split across multiple `recv()` calls on the server, or multiple writes may be coalesced into one read.

Therefore, SyncPulse never assumes one `recv()` equals one message. Every JSON header and ACK is length-prefixed, and the binary payload length is explicitly declared in the metadata header. Both sides use an exact-read loop (`read_exact`) that accumulates bytes until the exact target count is received or EOF/timeout occurs.

### 1.2 Wire Format

```
Client -> Worker:
+---------------------------+------------------------------+-----------------------------------+
| Metadata Length (4 bytes) | Metadata JSON (N bytes)      | Raw File Bytes (byte_size bytes)  |
| uint32 big-endian (!I)    | UTF-8 encoded JSON           | Streamed in 64 KiB chunks         |
+---------------------------+------------------------------+-----------------------------------+

Worker -> Client:
+---------------------------+------------------------------+
| ACK Length (4 bytes)      | ACK JSON (M bytes)           |
| uint32 big-endian (!I)    | UTF-8 encoded JSON           |
+---------------------------+------------------------------+
```

### 1.3 Metadata Schema (Client → Worker)

Maximum metadata size: **16 KiB (`16384` bytes)**.

```json
{
  "version": 1,
  "transfer_id": "550e8400-e29b-41d4-a716-446655440000",
  "original_filename": "vacation.jpg",
  "byte_size": 1048576,
  "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
}
```

Validation rules enforced by the Worker:
- `0 < metadata_length <= MAX_METADATA_BYTES` (16 KiB)
- Valid UTF-8 JSON object
- `version == 1`
- `transfer_id` is a valid canonical UUID string (prevents path traversal; `original_filename` is never used in filesystem paths)
- `byte_size` is an integer with `0 <= byte_size <= MAX_FILE_SIZE_BYTES` (default 50 MiB)
- `sha256` is a 64-character lowercase hexadecimal string

### 1.4 ACK Schema (Worker → Client)

```json
{
  "version": 1,
  "transfer_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "OK",
  "bytes_written": 1048576,
  "stored_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "error_code": null,
  "message": "Transfer verified and committed"
}
```

Error codes:
- `INVALID_METADATA_LENGTH`
- `INVALID_JSON`
- `UNSUPPORTED_VERSION`
- `INVALID_TRANSFER_ID`
- `INVALID_METADATA`
- `FILE_TOO_LARGE`
- `INCOMPLETE_TRANSFER`
- `CHECKSUM_MISMATCH`
- `DUPLICATE_CONFLICT`
- `TIMEOUT`
- `STORAGE_ERROR`

### 1.5 Why SHA-256 Is Still Needed Over TCP

TCP's 16-bit checksum detects accidental bit flips on the wire at the segment level, but it does **not** protect against:
- Application-level framing bugs or premature connection truncation
- Disk write corruption or partial file flushes
- Process crashes mid-stream
- End-to-end verification between the staged file on the API Server and the committed `.blob` file on the Backup Worker

### 1.6 Idempotency & Duplicate Handling

If the TCP connection drops right before the Worker's ACK reaches the API Server, the Worker has already committed `data/backups/<transfer_id>.blob`, while the API Server marks the transfer `FAILED`. When the user retries the same `transfer_id`:
- **Existing `.blob` matches `byte_size` and recomputed `sha256`**: The Worker drains the incoming stream cleanly and responds with `status: "OK"` (`message: "Already verified (idempotent)"`).
- **Existing `.blob` has a different size or `sha256`**: The Worker responds with `status: "ERROR"` and `error_code: "DUPLICATE_CONFLICT"`.
- **No final `.blob` exists**: The Worker writes to a fresh `.part` file, verifies, and atomically replaces via `os.replace()`.

---

## 2. UDP Heartbeat Protocol

### 2.1 Why UDP for Heartbeats

Heartbeats are periodic, small, self-contained liveness signals where **freshness matters more than retransmission**. If a 1-second heartbeat is lost, retransmitting an old heartbeat (as TCP would) is counterproductive because the next heartbeat arrives 1 second later. UDP avoids connection setup overhead and head-of-line blocking.

### 2.2 Datagram Format

Each heartbeat is a single UTF-8 JSON UDP datagram (< 512 bytes to avoid IP fragmentation):

```json
{
  "version": 1,
  "worker_id": "backup-worker-1",
  "session_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "sequence_number": 42,
  "timestamp_utc": "2026-11-20T03:40:00.123456+00:00",
  "uptime_seconds": 42.1,
  "cpu_percent": 12.5,
  "memory_percent": 48.2
}
```

### 2.3 Liveness State Machine & Timing

- **Default send interval**: `1.0s` (`SYNCPULSE_HEARTBEAT_INTERVAL_SECONDS`)
- **Default unresponsive timeout**: `4.0s` (`SYNCPULSE_HEARTBEAT_TIMEOUT_SECONDS`)
  *(Experimental local defaults, not universal production standards.)*

State transitions are determined using the **receiver's monotonic/UTC arrival time**, never solely trusting the sender's clock:

1. `STARTING` → `HEALTHY` on first valid heartbeat (`WORKER_HEALTHY` event)
2. `HEALTHY` → `UNRESPONSIVE` when `now - last_received > timeout` (`WORKER_UNRESPONSIVE` event)
3. `UNRESPONSIVE` → `HEALTHY` when a heartbeat arrives again (`WORKER_RECOVERED` event)

Steady-state heartbeats update in-memory state and `worker_state` without spamming duplicate `health_events` rows every second.

### 2.4 Sequence Gaps vs. Process Restarts

- When `session_id` stays the same and `sequence_number > last_sequence_number + 1`, the receiver records a **suspected missed heartbeat gap**. Because UDP is connectionless, a gap does not prove physical network packet loss—it can reflect sender drop, OS scheduling delay, or controlled fault injection (`SYNCPULSE_UDP_DROP_EVERY_N`).
- When `session_id` changes, the receiver recognizes a **worker restart** and resets the sequence baseline without miscounting the reset as packet loss.
