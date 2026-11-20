# Agentic AI Development Log

This document transparently records how Agentic AI was used during the design, implementation, testing, and verification of **SyncPulse** for Computer Networks Assignment No. 4.

---

## Entry 1 — Architecture & Protocol Specification

- **Date:** 2026-11-20
- **Task:** Project initialization, system architecture, and TCP/UDP wire protocol design
- **Agentic AI Tool:** Antigravity Agent
- **Prompt Summary:** Design a self-hosted file backup and infrastructure monitoring system (`SyncPulse`) using custom TCP sockets for file transfer and UDP datagrams for heartbeats, runnable for free on Windows + iPhone Safari.
- **Generated/Modified Files:** `.gitignore`, `.env.example`, `backend/config.py`, `backend/requirements.txt`, `docs/architecture.md`, `docs/protocol-design.md`
- **AI's Contribution:** Structured the two-process boundary (API Server vs. Backup Worker), defined 4-byte big-endian length-prefixed JSON framing for TCP, and defined the UDP heartbeat state machine (`STARTING`, `HEALTHY`, `UNRESPONSIVE`, `RECOVERED`).
- **Human Review & Verification:** Reviewed port bindings so only HTTP `:8000` binds to `0.0.0.0` while raw TCP `:9101` and UDP `:9102` default to `127.0.0.1`.

---

## Entry 2 — Custom TCP File Transfer Server, Client & Framing Tests

- **Date:** 2026-11-20
- **Task:** Implement raw asyncio TCP server/client with exact-length reads, SHA-256 verification, atomic file commits, and idempotency
- **Agentic AI Tool:** Antigravity Agent
- **Generated/Modified Files:** `backend/networking/protocol.py`, `backend/networking/tcp_server.py`, `backend/networking/tcp_client.py`, `tests/unit/test_protocol.py`, `tests/integration/test_tcp_transfer.py`
- **AI's Contribution:** Implemented `read_exact()` / `read_json_frame()` framing, streaming `.part` write + `os.replace()` commit, idempotent duplicate `transfer_id` handling (draining stream cleanly on match; rejecting mismatched content with `DUPLICATE_CONFLICT`), and 23 automated unit/integration tests including 7-byte fragmented TCP writes.
- **Problem Encountered:** `test_worker_not_running_returns_unreachable` failed on Windows with `CONNECTION_TIMEOUT` instead of `WORKER_UNREACHABLE` when `timeout=2.0`.
- **Root Cause & Fix:** Windows TCP loopback retries SYN packets for ~2 seconds before returning `WSAECONNREFUSED`. Increased test timeout to `5.0` (matching realistic client timeout vs. default `30.0`).
- **Verification Result:** `23 passed` in `pytest`.

---

## Entry 3 — UDP Heartbeat Sender, Receiver & Liveness State Machine

- **Date:** 2026-11-20
- **Task:** Implement real UDP socket heartbeat transmission, receiver watchdog, and session-aware state transitions
- **Agentic AI Tool:** Antigravity Agent
- **Generated/Modified Files:** `backend/networking/udp_heartbeat.py`, `tests/integration/test_udp_heartbeat.py`
- **AI's Contribution:** Implemented `UDPHeartbeatSender` (with optional `drop_every_n` controlled fault injection), `HeartbeatStateMachine` (using receiver monotonic arrival time rather than sender clocks, and distinguishing `session_id` restarts from missed heartbeats), and `UDPHeartbeatReceiver`.
- **Verification Result:** `4 passed` in `pytest`.

---

## Entry 4 — SQLite Persistence, Services, Worker Process & FastAPI REST API

- **Date:** 2026-11-20
- **Task:** Connect networking layers to SQLite, sequential transfer queue, FastAPI endpoints, and standalone worker CLI
- **Agentic AI Tool:** Antigravity Agent
- **Generated/Modified Files:** `backend/database.py`, `backend/schemas.py`, `backend/services/*`, `backend/api/*`, `backend/app.py`, `backend/worker.py`, `tests/unit/test_storage_and_state.py`, `tests/integration/test_api.py`, `tests/failure/test_fault_injection.py`
- **Problem Encountered:** Starlette emitted a deprecation warning for `status.HTTP_413_REQUEST_ENTITY_TOO_LARGE` in `backend/api/transfers.py`.
- **Fix:** Updated to `status.HTTP_413_CONTENT_TOO_LARGE`.
- **Verification Result:** Full suite `37 passed, 0 failed` including worker shutdown/recovery/retry and mid-transfer interruption fault injection tests.

---

## Entry 5 — Mobile-First React Frontend, Runner Scripts & Live Two-Process Smoke Test

- **Date:** 2026-11-20
- **Task:** Build responsive web console for iPhone Safari and Windows desktop, serve production bundle via FastAPI, and verify two independent OS processes live
- **Agentic AI Tool:** Antigravity Agent
- **Generated/Modified Files:** `frontend/*`, `scripts/run-worker.ps1`, `scripts/run-api.ps1`, `scripts/run-tests.ps1`
- **AI's Contribution:** Built Dashboard (separating UDP liveness from TCP backup health), File Backup (separate real XHR HTTP upload progress and TCP worker transfer progress), and Activity History (KST timestamps).
- **Verification Result:** `npm run build` (`tsc -b && vite build`) succeeded with 0 errors; live two-process Windows smoke test confirmed SPA serving, live UDP `HEALTHY`, real TCP file backup `VERIFIED` (`sha_match=True`), and `UNRESPONSIVE` detection upon terminating the worker process.
