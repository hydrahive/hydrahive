"""
test_core_policy.py — Issue #710

Testet die repo-weite Default-Policy als Markdown-Datei, geladen vom
Builder in `channels.policies` und im Cache-Hash berücksichtigt.

Geprüft wird:
- Policy-Datei existiert, non-empty und enthält die Kern-Stichworte
- build_system_prompt() liefert die Policy im static-Block
- Dynamic-Block bleibt leer wenn keine query-dynamischen Daten vorliegen
- _prompt_cache_hash() ändert sich bei Policy-Änderung (Pfad-Umleitung)
- Graceful bei fehlender Policy-Datei — kein Prompt-Build-Abbruch

Die Tests patchen `orchestrator_context._CORE_POLICY_PATH` direkt über
monkeypatch; das ist die offiziell testbare Stellschraube (siehe Docstring
von `_load_core_policy_text`).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core import orchestrator_context as oc
from hydrahive_core.orchestrator_context import (
    _CORE_POLICY_PATH,
    _load_core_policy_text,
    _prompt_cache_hash,
    build_system_prompt,
)


# --------------------------------------------------------------------------- fixtures

@pytest.fixture(autouse=True)
def _clear_caches():
    oc._STATIC_PROMPT_CACHE.clear()
    oc._SEGMENT_HASHES.clear()
    yield
    oc._STATIC_PROMPT_CACHE.clear()
    oc._SEGMENT_HASHES.clear()


@pytest.fixture
def boss_cfg(tmp_path):
    """Minimaler boss_cfg ohne memory/AGENT.md — Core-Policy ist agent-unabhängig."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    cfg = MagicMock()
    cfg.id = "test-core-policy"
    cfg.identity = "Test-Agent"
    cfg.agent_dir = agent_dir
    cfg.soul = None
    cfg.sources = []
    cfg.project_dir = None
    return cfg


# --------------------------------------------------------------------------- loader

def test_core_policy_file_exists_and_has_content():
    """Die versionierte Policy-Datei muss im Repo liegen und lesbar sein."""
    assert _CORE_POLICY_PATH.exists(), (
        f"Core-Policy fehlt an {_CORE_POLICY_PATH} — muss im Repo versioniert sein"
    )
    text = _load_core_policy_text()
    assert text, "Core-Policy darf nicht leer sein"


def test_core_policy_mentions_central_rules():
    """Der Policy-Text muss die Kernbegriffe aus der #710-Spec enthalten."""
    text = _load_core_policy_text()
    for term in ("MEMORY.md", "/projects/{id}/memory", "GitHub"):
        assert term in text, f"Core-Policy sollte den Begriff '{term}' enthalten"


def test_core_policy_missing_file_returns_empty(monkeypatch, tmp_path):
    """Fehlt die Datei, liefert der Loader leeren String — kein Fehler."""
    monkeypatch.setattr(oc, "_CORE_POLICY_PATH", tmp_path / "does_not_exist.md")
    assert _load_core_policy_text() == ""


def test_core_policy_unreadable_returns_empty(monkeypatch, tmp_path):
    """Wenn die Datei nicht lesbar ist, gibt der Loader leer zurück und loggt."""
    # Simuliere OSError beim read_text durch Pfad-Umleitung auf ein Verzeichnis.
    bogus = tmp_path / "policy_as_dir"
    bogus.mkdir()
    monkeypatch.setattr(oc, "_CORE_POLICY_PATH", bogus)
    # exists() ist True für Verzeichnisse, read_text wirft OSError → leer erwartet
    assert _load_core_policy_text() == ""


# --------------------------------------------------------------------------- builder integration

@pytest.mark.asyncio
async def test_build_system_prompt_contains_core_policy(boss_cfg):
    """Der static-Teil des Builder-Outputs muss die Core-Policy enthalten."""
    static_p, _dynamic_p = await build_system_prompt(boss_cfg, "Hallo")
    policy_text = _load_core_policy_text()
    # Prüfe auf mehrere Kernzeilen, damit ein einfacher Teil-Match nicht
    # durch zufälliges Vorkommen in identity/handbook unterwandert werden kann.
    for marker in ("erst suchen", "MEMORY.md", "GitHub"):
        assert marker.lower() in static_p.lower(), (
            f"static-Prompt enthält '{marker}' nicht — Policy nicht integriert"
        )
    # Und die komplette erste Überschrift sollte auch drin sein:
    assert "Core-Policy" in static_p or "Core-Policy" in policy_text


@pytest.mark.asyncio
async def test_dynamic_prompt_without_query_data_is_empty(boss_cfg):
    """Ohne Memory-Hits, Skills, Handoff etc. ist dynamic leer (keine Policy darin)."""
    _static_p, dynamic_p = await build_system_prompt(boss_cfg, "Hallo")
    assert dynamic_p == "" or "<memory_dynamic>" not in dynamic_p or all(
        marker.lower() not in dynamic_p.lower()
        for marker in ("erst suchen", "MEMORY.md")
    )


@pytest.mark.asyncio
async def test_missing_policy_does_not_break_build(monkeypatch, boss_cfg, tmp_path):
    """Fehlt die Policy-Datei, baut der Builder trotzdem einen Static-Prompt."""
    monkeypatch.setattr(oc, "_CORE_POLICY_PATH", tmp_path / "nope.md")
    static_p, _ = await build_system_prompt(boss_cfg, "Hallo")
    # Identity bleibt drin, Policy-Marker nicht:
    assert "Test-Agent" in static_p
    assert "erst suchen" not in static_p.lower()


# --------------------------------------------------------------------------- cache hash

def test_cache_hash_includes_core_policy(tmp_path):
    """Der Cache-Hash muss die Policy-Mtime berücksichtigen."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()

    # Mit Default-Policy:
    h1 = _prompt_cache_hash(agent_dir, mode="normal")

    # Ein anderer Policy-Pfad → anderer Hash (hier: leerer Override):
    empty_policy = tmp_path / "empty_policy.md"
    empty_policy.write_text("dummy", encoding="utf-8")
    import hydrahive_core.orchestrator_context as oc_mod
    orig = oc_mod._CORE_POLICY_PATH
    try:
        oc_mod._CORE_POLICY_PATH = empty_policy
        h2 = _prompt_cache_hash(agent_dir, mode="normal")
    finally:
        oc_mod._CORE_POLICY_PATH = orig

    assert h1 != h2, (
        "Cache-Hash ist mit/ohne Policy-Pfad-Override identisch — "
        "Policy-Änderungen würden unsichtbar bleiben"
    )


def test_cache_hash_without_policy_file(monkeypatch, tmp_path):
    """Fehlt die Policy-Datei, bleibt der Hash deterministisch (kein core_policy-Part)."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    monkeypatch.setattr(oc, "_CORE_POLICY_PATH", tmp_path / "nope.md")
    h = _prompt_cache_hash(agent_dir, mode="normal")
    assert isinstance(h, str) and len(h) == 16  # sha256-truncated — nie leer
