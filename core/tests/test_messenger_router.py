"""
test_messenger_router.py — v2 MessengerRouter (#590)

Testet rebuild() + resolve_*:
- WhatsApp Fallback (kein messenger.yaml → project_id als Route)
- WhatsApp session_ids explizit
- Discord/Telegram/Matrix Routes
- _deleted_ Prefix wird übersprungen
- Unbekannte IDs → None
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


@pytest.fixture
def projects_dir(tmp_path: Path) -> Path:
    # proj-a: kein messenger.yaml → Fallback wa[proj-a]=proj-a
    (tmp_path / "proj-a").mkdir()
    (tmp_path / "proj-a" / "config.yaml").write_text(
        'id: proj-a\nversion: "2.0.0"\nidentity:\n  name: a\n'
    )
    # proj-b: voller messenger.yaml
    (tmp_path / "proj-b").mkdir()
    (tmp_path / "proj-b" / "config.yaml").write_text(
        'id: proj-b\nversion: "2.0.0"\nidentity:\n  name: b\n'
    )
    (tmp_path / "proj-b" / "messenger.yaml").write_text(
        "whatsapp:\n"
        "  session_ids: ['wa-sess-1', 'wa-sess-2']\n"
        "discord:\n"
        "  channels: ['ch-123', 'ch-456']\n"
        "telegram:\n"
        "  chat_ids: [-100999, 42]\n"
        "matrix:\n"
        "  room: '!foo:matrix.org'\n"
    )
    # _deleted_proj-c: muss ignoriert werden
    (tmp_path / "_deleted_proj-c_99").mkdir()
    (tmp_path / "_deleted_proj-c_99" / "messenger.yaml").write_text(
        "whatsapp:\n  session_ids: ['ghost']\n"
    )
    return tmp_path


@pytest.fixture
def router(projects_dir: Path):
    from hydrahive_core.messenger_router import MessengerRouter
    r = MessengerRouter()
    r.rebuild(projects_dir=projects_dir)
    return r


# ============================================================= WhatsApp

def test_whatsapp_fallback_ohne_messenger_yaml(router):
    """Ohne messenger.yaml routet project_id 1:1 auf sich selbst."""
    assert router.resolve_whatsapp("proj-a") == "proj-a"


def test_whatsapp_session_ids_explizit(router):
    assert router.resolve_whatsapp("wa-sess-1") == "proj-b"
    assert router.resolve_whatsapp("wa-sess-2") == "proj-b"


def test_whatsapp_unbekannt_gibt_none(router):
    assert router.resolve_whatsapp("nicht-da") is None


# ============================================================= Discord

def test_discord_channel_routing(router):
    assert router.resolve_discord("ch-123") == "proj-b"
    assert router.resolve_discord("ch-456") == "proj-b"


def test_discord_unbekannt_gibt_none(router):
    assert router.resolve_discord("xxx") is None


# ============================================================= Telegram

def test_telegram_chat_id_string_lookup(router):
    # Negative IDs (Gruppen) werden auch als Strings indiziert
    assert router.resolve_telegram("-100999") == "proj-b"
    assert router.resolve_telegram("42") == "proj-b"


def test_telegram_integer_coercion(router):
    """resolve_telegram str(chat_id) → muss int-Input tolerieren."""
    assert router.resolve_telegram(42) == "proj-b"  # type: ignore[arg-type]


# ============================================================= Matrix

def test_matrix_room_routing(router):
    assert router.resolve_matrix("!foo:matrix.org") == "proj-b"


# ============================================================= _deleted_ Skip

def test_deleted_prefix_wird_ignoriert(router):
    """Ghost-Session aus _deleted_proj-c/ darf nicht indexiert werden."""
    assert router.resolve_whatsapp("ghost") is None
    # Keine Route mit "proj-c" im Value
    assert not any(v.endswith("proj-c") for v in router.all_routes()["whatsapp"].values())


# ============================================================= routes_for_project

def test_routes_for_project_liefert_alle_kanaele(router):
    routes = router.routes_for_project("proj-b")
    assert set(routes["whatsapp"]) == {"wa-sess-1", "wa-sess-2"}
    assert set(routes["discord"]) == {"ch-123", "ch-456"}
    assert set(routes["telegram"]) == {"-100999", "42"}
    assert routes["matrix"] == ["!foo:matrix.org"]


# ============================================================= rebuild ist idempotent

def test_rebuild_idempotent(projects_dir):
    from hydrahive_core.messenger_router import MessengerRouter
    r = MessengerRouter()
    r.rebuild(projects_dir=projects_dir)
    first = r.all_routes()
    r.rebuild(projects_dir=projects_dir)
    second = r.all_routes()
    assert first == second
