"""UDP Heartbeat sender (Backup Worker) and receiver/state machine (API Monitoring Service)."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import psutil

from backend.networking.protocol import PROTOCOL_VERSION

logger = logging.getLogger("syncpulse.udp_heartbeat")

MAX_UDP_DATAGRAM_BYTES = 2048


@dataclass(slots=True)
class HeartbeatPacket:
    version: int
    worker_id: str
    session_id: str
    sequence_number: int
    timestamp_utc: str
    uptime_seconds: float
    cpu_percent: float | None = None
    memory_percent: float | None = None

    def to_bytes(self) -> bytes:
        payload = {
            "version": self.version,
            "worker_id": self.worker_id,
            "session_id": self.session_id,
            "sequence_number": self.sequence_number,
            "timestamp_utc": self.timestamp_utc,
            "uptime_seconds": round(self.uptime_seconds, 2),
            "cpu_percent": self.cpu_percent,
            "memory_percent": self.memory_percent,
        }
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_bytes(cls, raw: bytes) -> HeartbeatPacket:
        if len(raw) > MAX_UDP_DATAGRAM_BYTES:
            raise ValueError(f"UDP datagram too large: {len(raw)} bytes")
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Heartbeat payload must be a JSON object")
        version = data.get("version")
        if version != PROTOCOL_VERSION:
            raise ValueError(f"Unsupported heartbeat version: {version!r}")
        worker_id = data.get("worker_id")
        session_id = data.get("session_id")
        seq = data.get("sequence_number")
        ts = data.get("timestamp_utc")
        uptime = data.get("uptime_seconds")
        if not isinstance(worker_id, str) or not worker_id:
            raise ValueError("Invalid worker_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("Invalid session_id")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
            raise ValueError("Invalid sequence_number")
        if not isinstance(ts, str) or not ts:
            raise ValueError("Invalid timestamp_utc")
        if not isinstance(uptime, (int, float)) or isinstance(uptime, bool) or uptime < 0:
            raise ValueError("Invalid uptime_seconds")

        return cls(
            version=version,
            worker_id=worker_id,
            session_id=session_id,
            sequence_number=seq,
            timestamp_utc=ts,
            uptime_seconds=float(uptime),
            cpu_percent=data.get("cpu_percent"),
            memory_percent=data.get("memory_percent"),
        )


@dataclass(slots=True)
class WorkerHealthSnapshot:
    worker_id: str
    session_id: str | None = None
    status: str = "STARTING"  # STARTING | HEALTHY | UNRESPONSIVE
    last_sequence_number: int = 0
    last_seen_at: str | None = None
    last_seen_monotonic: float | None = None
    started_at: str | None = None
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    uptime_seconds: float | None = None
    last_interval_ms: float | None = None
    suspected_missed_heartbeats: int = 0
    total_heartbeats_received: int = 0
    last_transition_event: str | None = None
    worker_cpu_percent: float | None = None
    worker_memory_percent: float | None = None


@dataclass(slots=True)
class HealthTransitionEvent:
    worker_id: str
    session_id: str | None
    event_type: str  # WORKER_HEALTHY | WORKER_UNRESPONSIVE | WORKER_RECOVERED
    previous_status: str
    new_status: str
    details: str
    created_at: str


TransitionCallback = Callable[[HealthTransitionEvent, WorkerHealthSnapshot], None]
HeartbeatCallback = Callable[[WorkerHealthSnapshot], None]


class UDPHeartbeatSender:
    """Periodically sends UDP heartbeat datagrams from the Backup Worker process."""

    def __init__(
        self,
        target_host: str,
        target_port: int,
        worker_id: str = "backup-worker-1",
        interval_seconds: float = 1.0,
        drop_every_n: int = 0,
        session_id: str | None = None,
    ) -> None:
        self.target_host = target_host
        self.target_port = target_port
        self.worker_id = worker_id
        self.interval_seconds = interval_seconds
        self.drop_every_n = max(0, int(drop_every_n))
        self.session_id = session_id or str(uuid.uuid4())

        self._sock: socket.socket | None = None
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._sequence = 0
        self._start_monotonic = time.monotonic()

    async def start(self) -> None:
        if self._running:
            return
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        self._start_monotonic = time.monotonic()
        self._running = True
        self._task = asyncio.create_task(self._send_loop(), name="udp-heartbeat-sender")
        logger.info(
            "UDP Heartbeat Sender started (worker_id=%s, session_id=%s, target=%s:%d, interval=%.2fs, drop_every_n=%d)",
            self.worker_id,
            self.session_id,
            self.target_host,
            self.target_port,
            self.interval_seconds,
            self.drop_every_n,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        logger.info("UDP Heartbeat Sender stopped (session_id=%s)", self.session_id)

    def send_once(self) -> bool:
        """Build and transmit a single UDP heartbeat datagram. Returns False if intentionally dropped."""
        self._sequence += 1
        if self.drop_every_n > 0 and self._sequence % self.drop_every_n == 0:
            logger.warning(
                "Controlled fault injection: intentionally omitted UDP heartbeat seq=%d (drop_every_n=%d)",
                self._sequence,
                self.drop_every_n,
            )
            return False

        packet = HeartbeatPacket(
            version=PROTOCOL_VERSION,
            worker_id=self.worker_id,
            session_id=self.session_id,
            sequence_number=self._sequence,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            uptime_seconds=time.monotonic() - self._start_monotonic,
            cpu_percent=psutil.cpu_percent(interval=None),
            memory_percent=psutil.virtual_memory().percent,
        )
        assert self._sock is not None
        try:
            self._sock.sendto(packet.to_bytes(), (self.target_host, self.target_port))
            return True
        except OSError as exc:
            logger.warning("Failed to send UDP heartbeat seq=%d: %s", self._sequence, exc)
            return False

    async def _send_loop(self) -> None:
        while self._running:
            self.send_once()
            await asyncio.sleep(self.interval_seconds)


class HeartbeatStateMachine:
    """
    Tracks worker liveness strictly from receiver-observed arrival times.

    Does not trust historical persisted states as live; begins in STARTING until a real
    UDP datagram arrives.
    """

    def __init__(
        self,
        worker_id: str = "backup-worker-1",
        timeout_seconds: float = 4.0,
        on_transition: TransitionCallback | None = None,
        on_heartbeat: HeartbeatCallback | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self._on_transition = on_transition
        self._on_heartbeat = on_heartbeat
        self.snapshot = WorkerHealthSnapshot(worker_id=worker_id, status="STARTING")

    def process_packet(
        self,
        packet: HeartbeatPacket,
        now_monotonic: float | None = None,
        now_utc: datetime | None = None,
    ) -> HealthTransitionEvent | None:
        now_mono = time.monotonic() if now_monotonic is None else now_monotonic
        now_dt = datetime.now(timezone.utc) if now_utc is None else now_utc
        now_iso = now_dt.isoformat()

        snap = self.snapshot
        snap.worker_id = packet.worker_id
        previous_status = snap.status
        session_changed = snap.session_id is not None and snap.session_id != packet.session_id

        if session_changed:
            # Worker restarted with a new session_id; reset sequence baseline rather than
            # miscounting a restart as UDP packet loss.
            snap.last_sequence_number = 0
            snap.started_at = now_iso
        elif snap.session_id is None:
            snap.started_at = now_iso

        if snap.last_seen_monotonic is not None and not session_changed:
            snap.last_interval_ms = round((now_mono - snap.last_seen_monotonic) * 1000.0, 2)
        else:
            snap.last_interval_ms = None

        if not session_changed and snap.last_sequence_number > 0:
            expected_next = snap.last_sequence_number + 1
            if packet.sequence_number > expected_next:
                missed = packet.sequence_number - expected_next
                snap.suspected_missed_heartbeats += missed
                logger.info(
                    "Suspected %d missed heartbeat(s) for %s (seq jumped %d -> %d)",
                    missed,
                    packet.worker_id,
                    snap.last_sequence_number,
                    packet.sequence_number,
                )

        snap.session_id = packet.session_id
        snap.last_sequence_number = packet.sequence_number
        snap.last_seen_at = now_iso
        snap.last_seen_monotonic = now_mono
        snap.updated_at = now_iso
        snap.uptime_seconds = packet.uptime_seconds
        snap.worker_cpu_percent = packet.cpu_percent
        snap.worker_memory_percent = packet.memory_percent
        snap.total_heartbeats_received += 1

        event: HealthTransitionEvent | None = None
        if previous_status == "STARTING":
            snap.status = "HEALTHY"
            snap.last_transition_event = "WORKER_HEALTHY"
            event = HealthTransitionEvent(
                worker_id=snap.worker_id,
                session_id=snap.session_id,
                event_type="WORKER_HEALTHY",
                previous_status=previous_status,
                new_status="HEALTHY",
                details=f"Initial UDP heartbeat received (session={snap.session_id}, seq={packet.sequence_number})",
                created_at=now_iso,
            )
        elif previous_status == "UNRESPONSIVE":
            snap.status = "HEALTHY"
            snap.last_transition_event = "WORKER_RECOVERED"
            reason = "new worker session" if session_changed else "same session resumed"
            event = HealthTransitionEvent(
                worker_id=snap.worker_id,
                session_id=snap.session_id,
                event_type="WORKER_RECOVERED",
                previous_status=previous_status,
                new_status="HEALTHY",
                details=f"UDP heartbeat recovered ({reason}, session={snap.session_id}, seq={packet.sequence_number})",
                created_at=now_iso,
            )
        elif session_changed:
            # Worker restarted faster than the unresponsive timeout; record recovery/restart
            snap.status = "HEALTHY"
            snap.last_transition_event = "WORKER_RECOVERED"
            event = HealthTransitionEvent(
                worker_id=snap.worker_id,
                session_id=snap.session_id,
                event_type="WORKER_RECOVERED",
                previous_status=previous_status,
                new_status="HEALTHY",
                details=f"Worker restarted with new session_id={snap.session_id}",
                created_at=now_iso,
            )

        if self._on_heartbeat is not None:
            self._on_heartbeat(snap)
        if event is not None and self._on_transition is not None:
            self._on_transition(event, snap)
        return event

    def check_timeout(
        self,
        now_monotonic: float | None = None,
        now_utc: datetime | None = None,
    ) -> HealthTransitionEvent | None:
        snap = self.snapshot
        if snap.status != "HEALTHY" or snap.last_seen_monotonic is None:
            return None

        now_mono = time.monotonic() if now_monotonic is None else now_monotonic
        elapsed = now_mono - snap.last_seen_monotonic
        if elapsed <= self.timeout_seconds:
            return None

        now_iso = (datetime.now(timezone.utc) if now_utc is None else now_utc).isoformat()
        previous_status = snap.status
        snap.status = "UNRESPONSIVE"
        snap.updated_at = now_iso
        snap.last_transition_event = "WORKER_UNRESPONSIVE"
        event = HealthTransitionEvent(
            worker_id=snap.worker_id,
            session_id=snap.session_id,
            event_type="WORKER_UNRESPONSIVE",
            previous_status=previous_status,
            new_status="UNRESPONSIVE",
            details=f"No UDP heartbeat received for {elapsed:.2f}s (timeout={self.timeout_seconds:.2f}s)",
            created_at=now_iso,
        )
        if self._on_transition is not None:
            self._on_transition(event, snap)
        return event


class _HeartbeatDatagramProtocol(asyncio.DatagramProtocol):
    def __init__(self, state_machine: HeartbeatStateMachine) -> None:
        self._state_machine = state_machine

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            packet = HeartbeatPacket.from_bytes(data)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning("Ignoring invalid UDP heartbeat from %s: %s", addr, exc)
            return
        self._state_machine.process_packet(packet)

    def error_received(self, exc: Exception) -> None:
        logger.warning("UDP receiver socket error: %s", exc)


class UDPHeartbeatReceiver:
    """Listens on a real UDP socket and runs a timeout watchdog for worker liveness."""

    def __init__(
        self,
        host: str,
        port: int,
        state_machine: HeartbeatStateMachine,
    ) -> None:
        self.host = host
        self.port = port
        self.state_machine = state_machine
        self._transport: asyncio.DatagramTransport | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def bound_port(self) -> int:
        if self._transport is not None:
            sock = self._transport.get_extra_info("socket")
            if sock is not None:
                return int(sock.getsockname()[1])
        return self.port

    async def start(self) -> None:
        if self._running:
            return
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _HeartbeatDatagramProtocol(self.state_machine),
            local_addr=(self.host, self.port),
        )
        self._running = True
        self._watchdog_task = asyncio.create_task(self._watchdog_loop(), name="udp-heartbeat-watchdog")
        logger.info("UDP Heartbeat Receiver listening on %s:%d", self.host, self.bound_port)

    async def stop(self) -> None:
        self._running = False
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
            self._watchdog_task = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        logger.info("UDP Heartbeat Receiver stopped")

    async def _watchdog_loop(self) -> None:
        poll_interval = max(0.05, min(0.5, self.state_machine.timeout_seconds / 4.0))
        while self._running:
            self.state_machine.check_timeout()
            await asyncio.sleep(poll_interval)
