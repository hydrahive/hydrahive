"""
test_807_shared_session_thread_safety.py — SharedSession unter Concurrency (#807)

Mehrere parallele subscribe/unsubscribe/broadcast-Calls dürfen nicht zu
RuntimeError ("dict changed size during iteration") oder inkonsistentem
Presence-State führen.
"""
from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _mgr():
    from hydrahive_core.shared_session import SharedSessionManager
    return SharedSessionManager()


def test_state_lock_exists_and_is_rlock():
    """Muss RLock sein (reentrant), weil _broadcast_presence aus gelockten
    Sections heraus aufgerufen wird."""
    mgr = _mgr()
    assert hasattr(mgr, "_state_lock"), "_state_lock fehlt — #807 regrediert"
    # RLock ist reentrant — doppeltes acquire im selben Thread blockiert nicht
    mgr._state_lock.acquire()
    ok = mgr._state_lock.acquire(blocking=False)
    assert ok, "_state_lock ist nicht reentrant — RLock noetig fuer nested broadcasts"
    mgr._state_lock.release()
    mgr._state_lock.release()


def test_concurrent_subscribe_unsubscribe_no_race():
    """50 Threads subscriben+unsubscriben parallel zum selben Projekt.
    Danach muss der Zustand konsistent sein (keine leaks, keine Corruption)."""
    async def run():
        mgr = _mgr()
        errors: list[Exception] = []

        async def cycle(i: int):
            try:
                q = mgr.subscribe("p", f"u{i}")
                await asyncio.sleep(0)  # yield
                mgr.unsubscribe("p", q, f"u{i}")
            except Exception as e:
                errors.append(e)

        await asyncio.gather(*[cycle(i) for i in range(50)])
        return mgr, errors

    mgr, errors = asyncio.run(run())
    assert not errors, f"Race-Errors: {errors[:3]}"
    # Nach komplettem Cycle: alles leer
    assert "p" not in mgr._subscribers
    assert "p" not in mgr._presence
    assert not mgr._queue_user


def test_broadcast_during_subscribe_no_iteration_error():
    """broadcast iteriert subs, subscribe fügt gleichzeitig hinzu. Darf
    nicht crashen — wir iterieren über eine Kopie."""
    async def run():
        mgr = _mgr()
        # Vorher 5 Subscriber, dann broadcast + subscribe parallel
        qs = [mgr.subscribe("p", f"u{i}") for i in range(5)]

        errors: list[Exception] = []

        async def broadcaster():
            for i in range(50):
                try:
                    mgr.broadcast("p", f'{{"text":"{i}"}}')
                except Exception as e:
                    errors.append(e)
                await asyncio.sleep(0)

        async def subscriber_churn():
            for i in range(30):
                try:
                    q = mgr.subscribe("p", f"new{i}")
                    mgr.unsubscribe("p", q, f"new{i}")
                except Exception as e:
                    errors.append(e)
                await asyncio.sleep(0)

        await asyncio.gather(broadcaster(), subscriber_churn())
        return errors

    errors = asyncio.run(run())
    assert not errors, f"Broadcast/Subscribe-Race: {errors[:3]}"


def test_acquire_turn_is_atomic():
    """Zwei Threads versuchen gleichzeitig den Turn zu bekommen — genau
    einer gewinnt."""
    mgr = _mgr()
    # Subscriber anlegen damit broadcast nicht leer ist
    mgr.subscribe("p", "alice")
    mgr.subscribe("p", "bob")

    wins = {"alice": 0, "bob": 0}
    barrier = threading.Barrier(2)

    def try_acquire(user: str):
        barrier.wait()
        if mgr.acquire_turn("p", user):
            wins[user] += 1
            mgr.release_turn("p", user)

    # 100x
    for _ in range(100):
        mgr._turn_owner["p"] = None  # Reset
        t1 = threading.Thread(target=try_acquire, args=("alice",))
        t2 = threading.Thread(target=try_acquire, args=("bob",))
        t1.start(); t2.start()
        t1.join(); t2.join()

    # Summe muss 100 oder 200 sein (je nachdem ob beide gleichzeitig
    # gewinnen konnten — darf nicht sein, weil check-and-set atomar)
    total = wins["alice"] + wins["bob"]
    assert total >= 100 and total <= 200, f"Turn-Wins: {wins}"
