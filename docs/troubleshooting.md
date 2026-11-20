# SyncPulse Troubleshooting Guide

## 1. Real Issues Encountered & Resolved During Development

### A. Windows Loopback TCP `connect()` Timeout vs. `ConnectionRefusedError`
- **Symptom:** When testing `send_file_over_tcp` against an unbound `127.0.0.1` port with `timeout=2.0`, Windows timed out (`CONNECTION_TIMEOUT`) before returning `ConnectionRefusedError` (`WORKER_UNREACHABLE`).
- **Cause:** Windows TCP stack performs SYN retries on loopback for ~2 seconds before raising `WSAECONNREFUSED`.
- **Resolution:** Default `SYNCPULSE_TCP_TIMEOUT_SECONDS` is `30.0` (and test timeout set to `5.0`), so offline workers cleanly report `WORKER_UNREACHABLE`.

### B. Starlette `HTTP_413_REQUEST_ENTITY_TOO_LARGE` Deprecation Warning
- **Symptom:** `pytest` reported a `StarletteDeprecationWarning` on HTTP 413 responses.
- **Resolution:** Updated `backend/api/transfers.py` to use `status.HTTP_413_CONTENT_TOO_LARGE`.

---

## 2. iPhone Safari LAN Connection Checklist

If `http://<laptop-ip>:8000` does not load on iPhone Safari:

1. **Same Network:** Confirm both the Windows laptop and iPhone are on the exact same Wi-Fi SSID (or connect the laptop to the iPhone's Personal Hotspot).
2. **Campus / Public Wi-Fi Client Isolation:** Many university Wi-Fi networks block device-to-device traffic on the same subnet. Switch to a home router or iPhone Personal Hotspot for the live demo.
3. **Windows Defender Firewall:** Allow inbound TCP port `8000` on **Private** networks only:
   ```powershell
   New-NetFirewallRule -DisplayName "SyncPulse HTTP 8000 (Private)" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
   ```
   *(Do not open ports `9101` or `9102`; they stay bound to `127.0.0.1`.)*
4. **Verify Locally First:** Confirm `http://127.0.0.1:8000` works in your Windows browser before debugging Wi-Fi.

---

## 3. Wireshark Loopback Capture on Windows

Because TCP (`9101`) and UDP (`9102`) communicate over `127.0.0.1`, standard Wi-Fi adapters will not show those packets.

1. Install **Wireshark** with **Npcap** and make sure **"Support loopback traffic ('Npcap Loopback Adapter')"** is checked during Npcap installation.
2. In Wireshark, select **Adapter for loopback traffic capture**.
3. Apply display filters:
   - TCP file transfer only: `tcp.port == 9101`
   - UDP heartbeats only: `udp.port == 9102`
   - Both protocols: `tcp.port == 9101 or udp.port == 9102`
4. Inspect:
   - **TCP:** 3-way handshake (`SYN`, `SYN-ACK`, `ACK`), Follow TCP Stream showing the 4-byte length prefix + JSON metadata + file payload + JSON ACK, and `FIN`/`RST` on completion or worker kill.
   - **UDP:** 1-second datagrams with UTF-8 JSON heartbeat payloads and incrementing `sequence_number`.
