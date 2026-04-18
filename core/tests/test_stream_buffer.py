"""Tests für SharedSession-Stream-Buffer (#726 K2a).

Verifiziert dass Broadcast-Events während eines aktiven Streams im Buffer
landen, bei end() geleert werden und late-subscriber via snapshot() die
bisherigen Events abrufen können.
"""
from __future__ import annotations

from hydrahive_core.shared_session import ProjectStreamBuffer, SharedSessionManager


def test_stream_buffer_lifecycle():
    buf = ProjectStreamBuffer()
    assert not buf.is_active()
    assert buf.snapshot() == []

    buf.start("stream-1")
    assert buf.is_active()
    assert buf.stream_id == "stream-1"
    assert buf.snapshot() == []

    buf.append('{"text":"Hallo"}')
    buf.append('{"text":" Welt"}')
    assert buf.snapshot() == ['{"text":"Hallo"}', '{"text":" Welt"}']

    buf.end()
    assert not buf.is_active()
    assert buf.snapshot() == []


def test_stream_buffer_append_without_start_is_noop():
    buf = ProjectStreamBuffer()
    buf.append('{"text":"ignored"}')
    assert buf.snapshot() == []


def test_stream_buffer_caps_at_max_events():
    buf = ProjectStreamBuffer()
    buf.MAX_EVENTS = 3
    buf.start("s")
    for i in range(5):
        buf.append(f"evt-{i}")
    # Ältester wurde gekappt
    assert buf.snapshot() == ["evt-2", "evt-3", "evt-4"]


def test_manager_broadcast_feeds_active_stream_buffer():
    mgr = SharedSessionManager()
    pid = "proj-a"
    assert mgr.get_stream_snapshot(pid) == []

    mgr.start_stream(pid, "stream-xyz")
    mgr.broadcast(pid, '{"text":"first"}')
    mgr.broadcast(pid, '{"text":"second"}')
    assert mgr.get_stream_snapshot(pid) == ['{"text":"first"}', '{"text":"second"}']

    mgr.end_stream(pid)
    assert mgr.get_stream_snapshot(pid) == []


def test_manager_broadcast_without_active_stream_does_not_buffer():
    mgr = SharedSessionManager()
    pid = "proj-b"
    # kein start_stream → kein Buffering
    mgr.broadcast(pid, '{"text":"noise"}')
    assert mgr.get_stream_snapshot(pid) == []


def test_manager_second_start_resets_buffer():
    mgr = SharedSessionManager()
    pid = "proj-c"
    mgr.start_stream(pid, "stream-1")
    mgr.broadcast(pid, '{"text":"alt"}')
    mgr.start_stream(pid, "stream-2")
    assert mgr.get_stream_snapshot(pid) == []
    mgr.broadcast(pid, '{"text":"neu"}')
    assert mgr.get_stream_snapshot(pid) == ['{"text":"neu"}']
