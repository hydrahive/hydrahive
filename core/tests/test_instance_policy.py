"""#711: Instance-Policy — /etc/hydrahive/instance_policy.md als optionale Instanz-Schicht.

Tests:
- Fehlende Datei → kein Fehler, kein instance_policy-Channel
- Leere Datei → ebenfalls kein Channel
- Vorhandene Datei → landet in channels.instance_policy
- Cache-Hash ändert sich wenn instance_policy.md sich ändert
- settings.instance_policy zeigt auf korrekten Pfad
"""
from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.context_channels import ContextChannels
from hydrahive_core import settings as settings_module


# ── Hilfsfunktionen ─────────────────────────────────────────────────────────

def _make_boss_cfg(tmp_path: Path, agent_dir: Path | None = None) -> MagicMock:
    cfg = MagicMock()
    cfg.id = "test-agent"
    cfg.agent_dir = agent_dir
    cfg.soul = None
    cfg.identity = "TestAgent"
    cfg.sources = []
    cfg.project_dir = None
    cfg.llm = None
    return cfg


# ── 1. settings.instance_policy ─────────────────────────────────────────────

def test_settings_instance_policy_path(tmp_path):
    """settings.instance_policy zeigt auf /etc/hydrahive/instance_policy.md."""
    from hydrahive_core.settings import HydraHiveSettings
    s = HydraHiveSettings(etc_dir=tmp_path)
    assert s.instance_policy == tmp_path / "instance_policy.md"


# ── 2. ContextChannels hat instance_policy Slot ──────────────────────────────

def test_context_channels_has_instance_policy_slot():
    ch = ContextChannels()
    assert hasattr(ch, "instance_policy")
    assert ch.instance_policy == ""


def test_context_channels_instance_policy_in_static_slots():
    assert "instance_policy" in ContextChannels._STATIC_SLOTS


def test_context_channels_instance_policy_rendered_in_static_str():
    ch = ContextChannels()
    ch.instance_policy = "## Instanz-Regel\n\nKein sudo."
    result = ch.to_static_str()
    assert "Instanz-Regel" in result
    assert "Kein sudo." in result


def test_context_channels_instance_policy_order():
    """instance_policy kommt nach handbook, vor blueprint."""
    slots = ContextChannels._STATIC_SLOTS
    hi = slots.index("handbook")
    ii = slots.index("instance_policy")
    bi = slots.index("blueprint")
    assert hi < ii < bi


# ── 3. Cache-Hash ───────────────────────────────────────────────────────────

def test_prompt_cache_hash_changes_on_instance_policy_change(tmp_path):
    """Hash ändert sich wenn instance_policy.md sich ändert."""
    from hydrahive_core.orchestrator_context import _prompt_cache_hash

    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()

    policy_file = tmp_path / "etc" / "instance_policy.md"
    policy_file.parent.mkdir()

    with patch.object(settings_module.settings, "instance_policy", policy_file):
        # Ohne Datei
        h1 = _prompt_cache_hash(agent_dir, "full")

        # Datei anlegen
        policy_file.write_text("Regel A")
        h2 = _prompt_cache_hash(agent_dir, "full")
        assert h1 != h2, "Hash muss sich beim Anlegen der Datei ändern"

        # Datei ändern (mtime manipulieren)
        time.sleep(0.01)
        policy_file.write_text("Regel B")
        policy_file.touch()  # sicherstellt neues mtime
        h3 = _prompt_cache_hash(agent_dir, "full")
        assert h2 != h3, "Hash muss sich beim Ändern der Datei ändern"


# ── 4. build_system_prompt — fehlende/leere/vorhandene Datei ─────────────────

@pytest.mark.asyncio
async def test_build_system_prompt_missing_instance_policy(tmp_path):
    """Fehlende instance_policy.md → kein Fehler, instance_policy-Channel leer."""
    from hydrahive_core.orchestrator_context import build_system_prompt

    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    policy_path = tmp_path / "etc" / "instance_policy.md"
    policy_path.parent.mkdir()
    # Datei existiert NICHT

    boss_cfg = _make_boss_cfg(tmp_path, agent_dir)

    with patch.object(settings_module.settings, "instance_policy", policy_path), \
         patch.object(settings_module.settings, "system_handbook", tmp_path / "handbook.md"):
        static, dynamic = await build_system_prompt(boss_cfg, "hallo")

    # Kein Fehler, kein instance_policy-Text im Prompt
    full = static + dynamic
    assert "Instance Policy" not in full


@pytest.mark.asyncio
async def test_build_system_prompt_empty_instance_policy(tmp_path):
    """Leere instance_policy.md → Channel bleibt leer."""
    from hydrahive_core.orchestrator_context import build_system_prompt

    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    policy_path = tmp_path / "etc" / "instance_policy.md"
    policy_path.parent.mkdir()
    policy_path.write_text("   \n\n  ")  # nur Whitespace

    boss_cfg = _make_boss_cfg(tmp_path, agent_dir)

    with patch.object(settings_module.settings, "instance_policy", policy_path), \
         patch.object(settings_module.settings, "system_handbook", tmp_path / "handbook.md"):
        static, dynamic = await build_system_prompt(boss_cfg, "hallo")

    full = static + dynamic
    assert "Instance Policy" not in full


@pytest.mark.asyncio
async def test_build_system_prompt_with_instance_policy(tmp_path):
    """Vorhandene instance_policy.md → landet im Static-Prompt."""
    from hydrahive_core.orchestrator_context import build_system_prompt

    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    policy_path = tmp_path / "etc" / "instance_policy.md"
    policy_path.parent.mkdir()
    policy_path.write_text("## Instanzregel\n\nKein Download von externen Quellen.")

    boss_cfg = _make_boss_cfg(tmp_path, agent_dir)

    with patch.object(settings_module.settings, "instance_policy", policy_path), \
         patch.object(settings_module.settings, "system_handbook", tmp_path / "handbook.md"):
        static, dynamic = await build_system_prompt(boss_cfg, "hallo")

    assert "Instanzregel" in static
    assert "Kein Download von externen Quellen." in static


# ── 5. Cache-Invalidierung bei geänderter instance_policy ───────────────────

@pytest.mark.asyncio
async def test_cache_invalidated_when_instance_policy_changes(tmp_path):
    """Änderung der instance_policy.md invalidiert den Static-Prompt-Cache."""
    from hydrahive_core.orchestrator_context import build_system_prompt, _STATIC_PROMPT_CACHE

    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    policy_path = tmp_path / "etc" / "instance_policy.md"
    policy_path.parent.mkdir()
    policy_path.write_text("Regel v1")

    boss_cfg = _make_boss_cfg(tmp_path, agent_dir)

    with patch.object(settings_module.settings, "instance_policy", policy_path), \
         patch.object(settings_module.settings, "system_handbook", tmp_path / "handbook.md"):

        static1, _ = await build_system_prompt(boss_cfg, "test")
        assert "Regel v1" in static1

        # Datei ändern + mtime sicherstellen
        time.sleep(0.05)
        policy_path.write_text("Regel v2")
        policy_path.touch()

        static2, _ = await build_system_prompt(boss_cfg, "test")
        assert "Regel v2" in static2
        assert static1 != static2
