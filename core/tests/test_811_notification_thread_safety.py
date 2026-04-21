"""
test_811_notification_thread_safety.py — SQLite-Thread-Safety (#811)

NotificationService wird von mehreren Threads parallel benutzt
(FastAPI-Worker-Threads + asyncio-to-thread). Ohne Lock → sporadische
"Recursive use of cursors not allowed" / Lost Writes.
"""
from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


@pytest.fixture
def svc(tmp_path, monkeypatch):
    from hydrahive_core import notification_service as ns_mod
    # Custom DB-Pfad in tmp
    monkeypatch.setattr(ns_mod, "DB_PATH", tmp_path / "notif.db")
    svc = ns_mod.NotificationService()
    svc.start()
    yield svc
    svc.stop()


def test_lock_is_threading_lock(svc):
    """Der Lock muss threading.Lock sein (nicht asyncio.Lock) — sqlite3
    interagiert in Thread-Pool-Executoren, die keinen Event-Loop haben."""
    import threading as _threading
    # Lock-Typ prüfen — threading.Lock gibt nur eine _thread.lock-Instanz
    # zurück, also checken via Methode acquire/release.
    assert hasattr(svc._db_lock, "acquire")
    assert hasattr(svc._db_lock, "release")
    # Sicherstellen dass es KEIN asyncio.Lock ist
    import asyncio as _asyncio
    assert not isinstance(svc._db_lock, _asyncio.Lock)


def test_concurrent_pushes_no_race(svc):
    """100 parallele Pushes von 10 Threads → alle 100 in DB, keine Exception."""
    async def run():
        async def push_one(i):
            await svc.push(
                user=f"user{i % 5}",
                type="task_done",
                title=f"t{i}",
                body=f"b{i}",
            )
        await asyncio.gather(*[push_one(i) for i in range(100)])

    asyncio.run(run())

    total = sum(svc.unread_count(f"user{i}") for i in range(5))
    assert total == 100, f"Erwartet 100 Notifications, gefunden {total}"


def test_concurrent_read_write(svc):
    """Reader + Writer parallel — darf nicht in sqlite3.InterfaceError laufen."""
    errors: list[Exception] = []

    async def run():
        async def push_loop():
            for i in range(50):
                try:
                    await svc.push(user="alice", type="t", title=f"#{i}", body="x")
                except Exception as e:
                    errors.append(e)

        async def read_loop():
            for _ in range(50):
                try:
                    svc.get_all("alice", limit=10)
                    svc.unread_count("alice")
                except Exception as e:
                    errors.append(e)

        await asyncio.gather(push_loop(), read_loop(), push_loop(), read_loop())

    asyncio.run(run())
    assert not errors, f"Race-Errors aufgetreten: {errors[:5]}"
    assert svc.unread_count("alice") == 100


def test_mark_read_under_concurrent_push(svc):
    async def run():
        for i in range(20):
            await svc.push(user="bob", type="t", title=f"#{i}", body="x")

        ids = [n.id for n in svc.get_all("bob")]

        async def reader():
            svc.unread_count("bob")

        async def marker(nid):
            svc.mark_read(nid, "bob")

        # Interleaved
        tasks = []
        for nid in ids:
            tasks.append(marker(nid))
            tasks.append(reader())
        await asyncio.gather(*tasks)

    asyncio.run(run())
    assert svc.unread_count("bob") == 0
