# SyncPulse Live Demo Script (5–7 Minutes)

## Opening Summary

> "SyncPulse is a self-hosted file backup and infrastructure monitoring system. It uses a custom framed TCP protocol for reliable file transfer with SHA-256 verification, and UDP heartbeats for lightweight service liveness monitoring."

---

## Pre-Demo Checklist (2 Minutes Before Presenting)

1. Connect Windows laptop and iPhone to the same private Wi-Fi or personal hotspot.
2. Ensure `frontend/dist` is built (`cd frontend && npm run build`).
3. Open two PowerShell terminals in `syncpulse/`:
   - Terminal 1: `.\scripts\run-worker.ps1`
   - Terminal 2: `.\scripts\run-api.ps1`
4. Open the printed `http://<laptop-ip>:8000` URL on iPhone Safari (and `http://127.0.0.1:8000` on laptop if projecting).
5. Optional: Open Wireshark on `Adapter for loopback traffic capture` with display filter:
   `tcp.port == 9101 or udp.port == 9102`

---

## Scene 1 — Problem & Architecture (45 sec)

- Explain the real problem: backing up files from a smartphone to a personal PC over local Wi-Fi without cloud storage fees, while verifying file integrity and knowing immediately if the backup worker stops.
- Point out the two-process architecture: FastAPI API Server (Process A) and independent Backup Worker (Process B) communicating over raw TCP (`127.0.0.1:9101`) and UDP (`127.0.0.1:9102`).

## Scene 2 — Normal File Backup from iPhone Safari (90 sec)

1. On iPhone Safari, tap **File Backup**.
2. Select a photo or document and tap **Start Backup**.
3. Point out the two distinct progress stages:
   - Stage 1: HTTP upload from Safari to FastAPI (staging)
   - Stage 2: Raw TCP stream from FastAPI to Backup Worker + SHA-256 verification
4. Show the resulting `VERIFIED` badge, SHA-256 match checkmark, and TCP throughput (MB/s).

## Scene 3 — Download & Integrity Verification (30 sec)

1. Tap **Download** on the verified backup entry.
2. Confirm the downloaded file opens cleanly and matches the original.

## Scene 4 — UDP Heartbeat Monitoring (60 sec)

1. Switch to **Dashboard**.
2. Point out:
   - **Backup Worker (UDP)**: `HEALTHY`, incrementing sequence number, last heartbeat timestamp (KST), and session ID.
   - **Backup Pipeline (TCP)**: `OK` — explain why liveness (UDP) and backup operation health (TCP) are tracked separately.
   - **Host Resource Utilization**: real local CPU, memory, and disk usage via `psutil`.
3. If Wireshark is open, show 1-second UDP datagrams on port `9102` and the TCP stream on port `9101`.

## Scene 5 — Fault Injection: Worker Shutdown (60 sec)

1. In Terminal 1 (`run-worker.ps1`), press `Ctrl+C` to stop the Backup Worker process.
2. Wait 4 seconds while watching the iPhone Dashboard:
   - Status automatically switches to `UNRESPONSIVE`.
3. Switch to **Activity** tab and show the `WORKER_UNRESPONSIVE` incident recorded in SQLite.
4. Attempt a file upload while the worker is down:
   - HTTP staging succeeds, TCP transfer fails cleanly (`FAILED` / `WORKER_UNREACHABLE`), and staging is preserved.

## Scene 6 — Recovery & Retry (60 sec)

1. Restart Terminal 1 (`.\scripts\run-worker.ps1`).
2. Within ~1–2 seconds, the Dashboard shows `HEALTHY` with a **new `session_id`**, and **Activity** logs `WORKER_RECOVERED`.
3. Go to **File Backup** and tap **Retry Backup** on the failed transfer.
4. Show the transfer completing as `VERIFIED` on Attempt #2.

## Scene 7 — Agentic AI Development Process (45 sec)

- Briefly summarize `docs/agentic-ai-log.md`:
  - AI assisted with protocol framing design, FastAPI/React implementation, and automated fault-injection tests.
  - Real debugging examples: handling TCP byte-stream partial reads (`read_exact`), Windows loopback SYN-retry timing in TCP tests, and separating UDP liveness from TCP transfer health.

---

## Backup Demo Recording Guide (If Live Wi-Fi Fails)

If campus Wi-Fi blocks client-to-client LAN traffic (AP isolation) during class:
1. Record a 3–5 minute screen capture (Windows Game Bar `Win+G` or OBS) showing Terminal 1, Terminal 2, browser dashboard, and Wireshark side-by-side.
2. Run through Scenes 2–6 on `http://127.0.0.1:8000` (or personal hotspot with iPhone) so you always have a verified fallback video.
