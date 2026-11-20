"""Integration and state-machine tests for real UDP heartbeat monitoring."""

from __future__ import annotations

import asyncio
import uuid

from backend.networking.udp_heartbeat import (
    HeartbeatPacket,
    HeartbeatStateMachine,
    HealthTransitionEvent,
    UDPHeartbeatReceiver,
    UDPHeartbeatSender,
)


async def test_udp_heartbeat_healthy_unresponsive_and_recovered() -> None:
    transitions: list[HealthTransitionEvent] = []
    sm = HeartbeatStateMachine(
        worker_id="backup-worker-1",
        timeout_seconds=0.4,
        on_transition=lambda ev, _snap: transitions.append(ev),
    )
    receiver = UDPHeartbeatReceiver(host="127.0.0.1", port=0, state_machine=sm)
    await receiver.start()

    sender = UDPHeartbeatSender(
        target_host="127.0.0.1",
        target_port=receiver.bound_port,
        worker_id="backup-worker-1",
        interval_seconds=0.1,
    )
    try:
        assert sm.snapshot.status == "STARTING"

        await sender.start()
        await asyncio.sleep(0.25)
        assert sm.snapshot.status == "HEALTHY"
        assert sm.snapshot.total_heartbeats_received >= 2
        assert len(transitions) == 1
        assert transitions[0].event_type == "WORKER_HEALTHY"

        # Stop sender -> timeout should trigger UNRESPONSIVE
        await sender.stop()
        await asyncio.sleep(0.55)
        assert sm.snapshot.status == "UNRESPONSIVE"
        assert len(transitions) == 2
        assert transitions[1].event_type == "WORKER_UNRESPONSIVE"

        # Restart with a new session_id -> should trigger WORKER_RECOVERED
        restarted_sender = UDPHeartbeatSender(
            target_host="127.0.0.1",
            target_port=receiver.bound_port,
            worker_id="backup-worker-1",
            interval_seconds=0.1,
        )
        await restarted_sender.start()
        try:
            await asyncio.sleep(0.25)
            assert sm.snapshot.status == "HEALTHY"
            assert sm.snapshot.session_id == restarted_sender.session_id
            assert sm.snapshot.suspected_missed_heartbeats == 0  # restart is not packet loss
            assert len(transitions) == 3
            assert transitions[2].event_type == "WORKER_RECOVERED"
        finally:
            await restarted_sender.stop()
    finally:
        await sender.stop()
        await receiver.stop()


async def test_controlled_udp_drop_every_n_detects_sequence_gap() -> None:
    sm = HeartbeatStateMachine(worker_id="backup-worker-1", timeout_seconds=2.0)
    receiver = UDPHeartbeatReceiver(host="127.0.0.1", port=0, state_machine=sm)
    await receiver.start()

    sender = UDPHeartbeatSender(
        target_host="127.0.0.1",
        target_port=receiver.bound_port,
        worker_id="backup-worker-1",
        interval_seconds=0.05,
        drop_every_n=3,  # Intentionally omit seq 3, 6, ...
    )
    try:
        await sender.start()
        await asyncio.sleep(0.35)
        assert sm.snapshot.status == "HEALTHY"
        assert sm.snapshot.suspected_missed_heartbeats >= 1
    finally:
        await sender.stop()
        await receiver.stop()


def test_state_machine_uses_receiver_time_not_sender_timestamp() -> None:
    sm = HeartbeatStateMachine(worker_id="w1", timeout_seconds=4.0)
    pkt = HeartbeatPacket(
        version=1,
        worker_id="w1",
        session_id=str(uuid.uuid4()),
        sequence_number=1,
        # Intentionally stale sender clock from 2020
        timestamp_utc="2020-01-01T00:00:00+00:00",
        uptime_seconds=1.0,
    )
    ev1 = sm.process_packet(pkt, now_monotonic=100.0)
    assert ev1 is not None and ev1.event_type == "WORKER_HEALTHY"
    assert sm.snapshot.status == "HEALTHY"

    # At t=103.5s (3.5s elapsed), still HEALTHY
    assert sm.check_timeout(now_monotonic=103.5) is None
    assert sm.snapshot.status == "HEALTHY"

    # At t=104.5s (4.5s elapsed), transitions to UNRESPONSIVE
    ev2 = sm.check_timeout(now_monotonic=104.5)
    assert ev2 is not None and ev2.event_type == "WORKER_UNRESPONSIVE"
    assert sm.snapshot.status == "UNRESPONSIVE"


def test_session_id_change_resets_sequence_without_false_loss() -> None:
    transitions: list[HealthTransitionEvent] = []
    sm = HeartbeatStateMachine(
        worker_id="w1",
        timeout_seconds=4.0,
        on_transition=lambda ev, _s: transitions.append(ev),
    )
    s1 = str(uuid.uuid4())
    s2 = str(uuid.uuid4())

    sm.process_packet(
        HeartbeatPacket(1, "w1", s1, 10, "2026-11-20T00:00:00+00:00", 10.0),
        now_monotonic=10.0,
    )
    sm.process_packet(
        HeartbeatPacket(1, "w1", s2, 1, "2026-11-20T00:00:01+00:00", 0.5),
        now_monotonic=11.0,
    )
    assert sm.snapshot.session_id == s2
    assert sm.snapshot.last_sequence_number == 1
    assert sm.snapshot.suspected_missed_heartbeats == 0
    assert transitions[-1].event_type == "WORKER_RECOVERED"
