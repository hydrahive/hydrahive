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


def _drain(queue):
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


def test_subscribe_primes_queue_with_buffer_snapshot():
    """K2b: frische Subscriber-Queue bekommt Buffer-Snapshot vor Live-Feed."""
    mgr = SharedSessionManager()
    pid = "proj-late"
    mgr.start_stream(pid, "s1")
    mgr.broadcast(pid, '{"text":"chunk-1"}')
    mgr.broadcast(pid, '{"text":"chunk-2"}')

    queue = mgr.subscribe(pid, "bob")
    texts = [e for e in _drain(queue) if '"text"' in e]
    assert texts == ['{"text":"chunk-1"}', '{"text":"chunk-2"}']


def test_subscribe_sees_live_events_after_priming():
    """K2b: Priming blockiert nicht Live-Events."""
    mgr = SharedSessionManager()
    pid = "proj-live"
    mgr.start_stream(pid, "s1")
    mgr.broadcast(pid, '{"text":"old"}')

    queue = mgr.subscribe(pid, "alice")
    mgr.broadcast(pid, '{"text":"neu"}')

    # Alte + neue Events drin, in Reihenfolge
    drained = _drain(queue)
    # Wir rechnen mit mindestens den beiden explizit gefeuerten Text-Events.
    # Presence-Events kommen zusätzlich — die filtern wir raus.
    texts = [e for e in drained if '"text"' in e]
    assert texts == ['{"text":"old"}', '{"text":"neu"}']


def test_subscribe_without_active_stream_primes_nothing():
    mgr = SharedSessionManager()
    pid = "proj-cold"
    queue = mgr.subscribe(pid, "user")
    # Queue ist leer außer evtl. Presence-Events
    drained = _drain(queue)
    assert all('"text"' not in e for e in drained)
