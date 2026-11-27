# SyncPulse Test & Verification Results

> **Policy:** Only tests actually executed in this workspace are marked `PASS`. Hardware-dependent manual checks (physical iPhone Safari over LAN, Wireshark/Npcap capture) are explicitly marked `PENDING (Manual)` with exact reproduction steps.

## 1. Execution Environment

- **OS:** Windows (win32)
- **Python:** 3.13.2 (`.venv`)
- **Node.js / npm:** v24.4.0 / 11.6.1
- **Test Runner:** `pytest 8.4.2`, `pytest-asyncio 0.26.0`, `httpx 0.28.1`
- **Frontend Build:** `tsc -b && vite build` (Vite 6.4.6)

---

## 2. Automated Test Suite Results

Command executed:
```powershell
.\scripts\run-tests.ps1
```

Summary: **37 passed, 0 failed** (re-verified after fixing Starlette `HTTP_413_CONTENT_TOO_LARGE` deprecation warning).

| Test ID | Category | Test Case | Purpose | Result |
|---|---|---|---|---|
| UT-01 | Unit (Protocol) | `test_validate_transfer_id_accepts_valid_uuid` | Canonical UUID accepted | PASS |
| UT-02 | Unit (Protocol) | `test_validate_transfer_id_rejects_invalid_and_traversal` (6 cases) | Blocks `../`, `..\\`, empty, non-UUID | PASS |
| UT-03 | Unit (Protocol) | `test_validate_sha256` | Normalizes & validates 64-char hex digest | PASS |
| UT-04 | Unit (Protocol) | `test_transfer_metadata_validation` | Checks protocol version & max file size | PASS |
| UT-05 | Unit (Protocol) | `test_read_exact_and_json_frame_handle_fragmented_stream` | 1-byte-at-a-time TCP stream framing | PASS |
| UT-06 | Unit (Protocol) | `test_read_json_frame_rejects_oversized_length` | Rejects metadata > `MAX_METADATA_BYTES` | PASS |
| UT-07 | Unit (Protocol) | `test_read_exact_raises_on_incomplete_stream` | Detects EOF before target byte count | PASS |
| UT-08 | Unit (Protocol) | `test_ack_roundtrip` | ACK serialization/deserialization | PASS |
| UT-09 | Unit (Storage/DB) | `test_sanitize_display_filename_strips_paths_and_unsafe_chars` | Cleans display filenames safely | PASS |
| UT-10 | Unit (Storage/DB) | `test_storage_service_blocks_path_traversal` | Rejects traversal in staging/backup paths | PASS |
| UT-11 | Unit (Storage/DB) | `test_save_staged_stream_enforces_size_limit_and_cleans_up` | Enforces upload cap & deletes partial | PASS |
| UT-12 | Unit (Storage/DB) | `test_database_recovers_interrupted_transfers_on_startup` | Marks stale `TRANSFERRING` rows `FAILED` | PASS |
| TCP-01 | Integration (TCP) | `test_small_file_transfer` | Small file end-to-end TCP + SHA-256 + ACK | PASS |
| TCP-02 | Integration (TCP) | `test_multi_chunk_file_transfer` | 300 KiB multi-chunk streaming + progress | PASS |
| TCP-03 | Integration (TCP) | `test_empty_file_transfer` | 0-byte file handled cleanly | PASS |
| TCP-04 | Integration (TCP) | `test_file_exceeding_max_size_rejected` | Rejects file > server max size | PASS |
| TCP-05 | Integration (TCP) | `test_fragmented_tcp_writes_handled_cleanly` | 7-byte fragmented TCP writes reassembled | PASS |
| TCP-06 | Integration (TCP) | `test_checksum_mismatch_rejected_and_cleaned_up` | Detects SHA-256 mismatch, deletes `.part` | PASS |
| TCP-07 | Integration (TCP) | `test_incomplete_transfer_cleans_up_part_file` | Mid-stream disconnect leaves no `.blob`/`.part` | PASS |
| TCP-08 | Integration (TCP) | `test_invalid_metadata_returns_error_ack` | Malformed JSON returns `INVALID_JSON` ACK | PASS |
| TCP-09 | Integration (TCP) | `test_duplicate_transfer_id_idempotency_and_conflict` | Same content -> idempotent OK; diff -> `DUPLICATE_CONFLICT` | PASS |
| TCP-10 | Integration (TCP) | `test_worker_not_running_returns_unreachable` | Unbound port returns `WORKER_UNREACHABLE` | PASS |
| UDP-01 | Integration (UDP) | `test_udp_heartbeat_healthy_unresponsive_and_recovered` | Real UDP `STARTING -> HEALTHY -> UNRESPONSIVE -> RECOVERED` | PASS |
| UDP-02 | Integration (UDP) | `test_controlled_udp_drop_every_n_detects_sequence_gap` | Controlled send omission increments suspected missed count | PASS |
| UDP-03 | Unit (UDP) | `test_state_machine_uses_receiver_time_not_sender_timestamp` | Ignores stale sender clock; uses receiver monotonic time | PASS |
| UDP-04 | Unit (UDP) | `test_session_id_change_resets_sequence_without_false_loss` | New `session_id` resets sequence baseline | PASS |
| API-01 | Integration (HTTP) | `test_health_worker_and_metrics_endpoints` | `/api/health`, `/api/worker/status`, `/api/system/metrics` | PASS |
| API-02 | Integration (HTTP) | `test_upload_verify_list_and_download_flow` | Upload -> TCP verify -> byte-exact download -> events | PASS |
| API-03 | Integration (HTTP) | `test_upload_exceeding_max_size_returns_413` | Oversize HTTP upload returns 413 | PASS |
| API-04 | Integration (HTTP) | `test_nonexistent_and_invalid_transfer_id_return_404` | Invalid UUID & missing ID return 404 | PASS |
| FI-01 | Failure Injection | `test_worker_shutdown_failure_recovery_and_retry` | Worker stop -> UNRESPONSIVE -> upload FAILED -> restart -> RECOVERED -> Retry VERIFIED | PASS |
| FI-02 | Failure Injection | `test_mid_transfer_interruption_leaves_no_corrupt_backup` | Throttled stream cut mid-transfer -> no `.blob` -> retry VERIFIED | PASS |

### Issue Discovered & Fixed During Testing

- **TCP-10 (Windows loopback SYN retry timing):** On Windows, connecting to an unbound `127.0.0.1` port retries SYN for ~2 seconds before raising `ConnectionRefusedError` (`WSAECONNREFUSED`). With `timeout=2.0`, the test initially received `CONNECTION_TIMEOUT` instead of `WORKER_UNREACHABLE`. Increasing the test timeout to `5.0` (production default is `30.0`) allowed Windows to return `WSAECONNREFUSED` deterministically, and TCP-10 passed on re-run.
- **API-03 deprecation warning:** Replaced deprecated `status.HTTP_413_REQUEST_ENTITY_TOO_LARGE` with `status.HTTP_413_CONTENT_TOO_LARGE`.

---

## 3. Live Two-Process Smoke Test (Windows)

Executed two independent OS processes (`python -m backend.worker` and `uvicorn backend.app:app`) with the production Vite build served from `frontend/dist`:

| Step | Observed Result | Status |
|---|---|---|
| Built React SPA served at `GET /` | `200 OK`, HTML served | PASS |
| Live UDP Heartbeat (Process B → Process A) | `liveness_status=HEALTHY`, `seq=7` | PASS |
| Real file upload + TCP socket transfer | `status=VERIFIED`, `sha_match=True`, `tcp_ms=31.47` | PASS |
| Kill Process B (`Stop-Process -Force`) | Transitioned to `UNRESPONSIVE` after timeout | PASS |

---

## 4. Manual Verification Checklist (Physical Device & Packet Capture)

| Test ID | Verification Item | Status | Observed Result / How to Verify |
|---|---|---|---|
| MAN-01 | iPhone Safari LAN access (`http://<laptop-ip>:8000`) | PASS (Manual, 2026-11-20) | Connected over local Wi-Fi; dashboard displayed live UDP `HEALTHY` (`seq #476`, interval `1014.64 ms`) and host resource telemetry |
| MAN-02 | iPhone Safari photo/document upload → VERIFIED → Download | PASS (Manual, 2026-11-20) | Uploaded `IMG_5183.png` (184.71 KB) and `IMG_5185.png` (252.73 KB) from iPhone Safari; both completed `VERIFIED` with SHA-256 match (TCP `3.505–4.75 MB/s`) and downloaded cleanly |
| MAN-03 | Wireshark loopback capture (`tcp.port == 9101` & `udp.port == 9102`) | PASS (Manual, 2026-11-20) | Captured on Npcap `Adapter for loopback traffic capture`: verified 64 KiB TCP streaming (`54109 -> 9101`), TCP JSON ACK (`Len=253`) & `FIN,ACK` teardown, and 1.0s UDP heartbeat datagrams (`63950 -> 9102`, `Len=231`, UTF-8 JSON payload with `worker_id` & `sequence_number`) |
