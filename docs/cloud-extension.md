# Design Rationale & Cloud Infrastructure Extension Roadmap

> **Honesty Note:** SyncPulse is currently implemented and verified **locally on a single Windows host** using loopback sockets (`127.0.0.1`), local disk storage, and SQLite. No paid AWS or cloud services were deployed for this assignment. This document explains the engineering trade-offs behind the current design and how the architecture maps to cloud infrastructure.

---

## 1. Key Architecture & Networking Design Decisions

1. **Why not use HTTP file upload alone?**
   - HTTP upload from Safari terminates at the web tier (`Process A`). Offloading storage persistence to an independent Backup Worker (`Process B`) over a custom framed TCP protocol demonstrates raw TCP stream framing, explicit acknowledgments, and decoupled backend storage workers.

2. **Why separate the Backup Worker into an independent OS process?**
   - Process isolation allows simulating real worker crashes (`Ctrl+C` / `Stop-Process`) while the API server and dashboard remain online, detect the outage via UDP heartbeats, preserve staged files, and recover cleanly on restart.

3. **TCP is already reliable—why add SHA-256 verification?**
   - TCP's 16-bit checksum only guards individual segments in transit. It does not detect application framing bugs, premature connection resets, partial disk flushes, or storage corruption. End-to-end SHA-256 verification before `os.replace()` guarantees that the committed `.blob` matches the staged input bit-for-bit.

4. **Why use UDP for heartbeats instead of TCP?**
   - Heartbeats are periodic, ephemeral liveness signals. If a 1-second heartbeat is dropped, retransmitting an old heartbeat (TCP behavior) wastes effort and adds head-of-line delay when a fresh heartbeat is already due. UDP avoids connection state and delivers low-overhead datagrams.

5. **Does a missing UDP heartbeat prove the worker is dead?**
   - No. Missing heartbeats can result from OS scheduling pauses, host CPU saturation, firewall drops, or controlled send omission (`SYNCPULSE_UDP_DROP_EVERY_N`). That is why the UI reports `UNRESPONSIVE` (not "confirmed dead") and labels sequence gaps as *suspected missed heartbeats*.

6. **What happens if the Worker dies mid-transfer?**
   - Incoming bytes are written only to `<transfer_id>.part`. On unexpected EOF or restart, `.part` files are deleted and never promoted to `.blob`. The API marks the transfer `FAILED`, retains `data/staging/<transfer_id>.part`, and offers a full-file **Retry**.

7. **What if the Worker commits the file, but the ACK is lost before reaching the API?**
   - The API marks the transfer `FAILED`, while the Worker holds `<transfer_id>.blob`. When the user retries the same `transfer_id`, the Worker recomputes the existing `.blob` SHA-256, sees an exact match, drains the stream, and replies `OK` (`Already verified (idempotent)`).

8. **What if the same `transfer_id` is sent with different file content?**
   - The Worker rejects it with `DUPLICATE_CONFLICT` so existing verified backups are never silently overwritten.

9. **How does a multi-process single host differ from a real distributed system?**
   - On `127.0.0.1`, loopback packets experience negligible latency, no physical router congestion, and shared host clock/disk fate. Real multi-host deployments introduce partial network partitions, asymmetric routing, clock skew, and independent disk failures.

10. **What changes would be needed for production cloud deployment?**
    - See the mapping table below.

---

## 2. Local Implementation vs. Future Cloud Roadmap

| Layer | Current Local Implementation (Built) | Future Cloud Extension (Planned / Not Implemented) |
|---|---|---|
| **Backup Storage** | Local disk (`data/backups/<id>.blob`) | Object storage (AWS S3 / GCS) with multipart upload & server-side encryption |
| **Compute** | Local Windows laptop processes | Linux VMs (EC2) or containerized services (Docker / ECS) |
| **Database** | Local SQLite (`WAL` mode) | Managed PostgreSQL (RDS / Cloud SQL) |
| **Monitoring** | Custom UDP receiver + SQLite `health_events` | Prometheus metrics + Alertmanager + Grafana dashboards |
| **Process Management** | Manual PowerShell scripts | `systemd` / container restart policies / orchestrator |
| **Scaling** | Single sequential TCP worker | Multiple workers behind a load balancer or message queue |
| **Security** | Trusted private LAN HTTP (no auth) | HTTPS (TLS 1.3), authentication (JWT/OAuth), mTLS between API & Worker |
