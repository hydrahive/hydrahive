"""
test_809_token_blacklist_persistent.py — persistente JWT-Blacklist (#809)

Vorher: in-memory dict, Restart = alle revokierten Tokens wieder gültig.
Jetzt: SQLite-Backend, überlebt Core-Neustart + Multi-Worker.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


def test_blacklist_persists_across_instances(tmp_path):
    from hydrahive_core.token_blacklist import TokenBlacklist

    db = tmp_path / "bl.db"
    exp_future = time.time() + 3600

    bl1 = TokenBlacklist(db)
    bl1.add("jti-abc", exp_future)
    assert bl1.is_revoked("jti-abc")
    bl1.close()

    # Neue Instanz auf selber DB → Revocation überlebt
    bl2 = TokenBlacklist(db)
    assert bl2.is_revoked("jti-abc") is True, \
        "Revocation ueberlebte den Restart nicht — #809 regrediert"
    assert bl2.is_revoked("unknown-jti") is False
    bl2.close()


def test_dict_compat_in_operator(tmp_path):
    """Bestandscode in main.py nutzt ``jti in _token_blacklist``."""
    from hydrahive_core.token_blacklist import TokenBlacklist
    bl = TokenBlacklist(tmp_path / "bl.db")
    bl.add("foo", time.time() + 60)
    assert "foo" in bl
    assert "bar" not in bl
    # Nicht-String-Keys dürfen nicht crashen
    assert 42 not in bl
    bl.close()


def test_dict_compat_setitem(tmp_path):
    """router_core_misc.logout nutzt ``_token_blacklist[jti] = exp``."""
    from hydrahive_core.token_blacklist import TokenBlacklist
    bl = TokenBlacklist(tmp_path / "bl.db")
    bl["foo"] = time.time() + 60
    assert bl.is_revoked("foo")
    bl.close()


def test_cleanup_expired_removes_only_expired(tmp_path):
    from hydrahive_core.token_blacklist import TokenBlacklist
    bl = TokenBlacklist(tmp_path / "bl.db")
    now = time.time()
    bl.add("old", now - 100)
    bl.add("fresh", now + 3600)
    removed = bl.cleanup_expired()
    assert removed == 1
    assert bl.is_revoked("fresh") is True
    assert bl.is_revoked("old") is False
    bl.close()


def test_is_revoked_self_heals_expired_entries(tmp_path):
    """Wenn ein Eintrag zwischen add() und is_revoked() abläuft, soll
    is_revoked False zurückgeben und den Eintrag gleich wegräumen."""
    from hydrahive_core.token_blacklist import TokenBlacklist
    bl = TokenBlacklist(tmp_path / "bl.db")
    bl.add("tmp", time.time() - 5)   # schon abgelaufen
    assert bl.is_revoked("tmp") is False
    # Cleanup sollte es jetzt nicht mehr finden
    assert bl.cleanup_expired() == 0
    bl.close()


def test_fallback_to_memory_on_init_error(tmp_path, monkeypatch):
    """Bei SQLite-Init-Error soll die Klasse auf in-memory fallbacken,
    nicht crashen — damit Core immer startet."""
    from hydrahive_core.token_blacklist import TokenBlacklist
    import sqlite3

    def _raise(*a, **kw):
        raise sqlite3.OperationalError("simulated")
    monkeypatch.setattr(sqlite3, "connect", _raise)

    bl = TokenBlacklist(tmp_path / "bl.db")
    # Memory-Mode: add + check funktioniert im aktuellen Prozess
    bl.add("x", time.time() + 60)
    assert bl.is_revoked("x")


def test_empty_jti_is_nop(tmp_path):
    from hydrahive_core.token_blacklist import TokenBlacklist
    bl = TokenBlacklist(tmp_path / "bl.db")
    bl.add("", time.time() + 60)
    assert not bl.is_revoked("")
    bl.close()
