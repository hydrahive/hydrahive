"""Tests für #668: User-Skill-Layer Runtime-Aktivierung im System-Prompt.

Der Resolver selbst ist schon in #659 getestet. Hier wird die Runtime-
Integration durch `build_system_prompt(request_user=...)` verifiziert:
- echter User → User-Skill landet im Prompt
- None/internal/invalid → User-Layer übersprungen
- Agent-Skill überschreibt User-Skill (Resolver-Prio bleibt)
- Cross-User-Cache-Regression: zwei Aufrufe für denselben Agent mit
  unterschiedlichen request_user dürfen keine Skills leaken.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.orchestrator_context import (
    _resolve_user_skills_dir,
    build_system_prompt,
    get_skill_tool_constraints,
)
from hydrahive_core.settings import settings


SKILL_TEMPLATE = """---
skill: {name}
version: "1.0"
scope: always
priority: {prio}
---

Body von {layer}: {name}.
"""

SKILL_WITH_TOOLS = """---
skill: {name}
version: "1.0"
scope: always
priority: {prio}
blocked_tools: [shell_exec]
---

Body von {layer}: {name}.
"""


def _write_skill(dirpath: Path, name: str, layer: str, prio: int = 50,
                 template: str = SKILL_TEMPLATE) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    p = dirpath / f"{name}.md"
    p.write_text(template.format(name=name, prio=prio, layer=layer),
                 encoding="utf-8")
    return p


def _make_cfg(agent_dir: Path, *, identity: str = "Test-Agent",
              agent_id: str = "test-agent") -> MagicMock:
    cfg = MagicMock()
    cfg.identity = identity
    cfg.soul = None
    cfg.agent_dir = agent_dir
    cfg.project_dir = None
    cfg.tools = []
    cfg.id = agent_id
    cfg.sources = []
    return cfg


@pytest.fixture(autouse=True)
def patch_user_skills_dir(tmp_path, monkeypatch):
    """Redirect `settings.user_skills_dir(username)` in ein Test-Tmpdir.

    Jeder Test bekommt `tmp_path / "users" / <name> / skills/` als
    User-Layer-Basis. Ungültige Usernames lösen weiterhin ValueError aus
    (Policy aus `skill_resolver.validate_username`).
    """
    base = tmp_path / "users"

    def _fake(self, username: str) -> Path:
        # Validation aus dem echten Resolver weiterverwenden:
        from hydrahive_core.skill_resolver import validate_username
        validate_username(username)
        return base / username / "skills"

    # Pydantic-Settings blockiert Instance-setattr — Klassen-Methode patchen.
    monkeypatch.setattr(type(settings), "user_skills_dir", _fake)
    return base


@pytest.fixture(autouse=True)
def clear_static_cache():
    """_STATIC_PROMPT_CACHE zwischen Tests leeren — sonst würden
    tmp_path-Agenten aus vorherigen Tests kollidieren."""
    from hydrahive_core.orchestrator_context import _STATIC_PROMPT_CACHE
    _STATIC_PROMPT_CACHE.clear()
    yield
    _STATIC_PROMPT_CACHE.clear()


# ────────────────────────────────────────────────────────────── _resolve_user_skills_dir

def test_resolver_helper_none_returns_none():
    assert _resolve_user_skills_dir(None) is None


def test_resolver_helper_internal_returns_none():
    assert _resolve_user_skills_dir("internal") is None


def test_resolver_helper_valid_returns_path(tmp_path, patch_user_skills_dir):
    got = _resolve_user_skills_dir("alice")
    assert got == patch_user_skills_dir / "alice" / "skills"


def test_resolver_helper_invalid_skips_with_warning(caplog):
    import logging
    caplog.set_level(logging.WARNING, logger="hydrahive_core.orchestrator_context")
    assert _resolve_user_skills_dir("../evil") is None
    assert any("ungültiger request_user" in r.message for r in caplog.records)


# ────────────────────────────────────────────────────────────── build_system_prompt

async def _prompt_str(cfg, *, request_user=None, user_text="Hallo"):
    static_p, dynamic_p = await build_system_prompt(
        cfg, user_text, request_user=request_user,
    )
    return (static_p + "\n\n" + dynamic_p).strip() if dynamic_p else static_p


async def test_user_skill_active_when_username(tmp_path, patch_user_skills_dir):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    _write_skill(patch_user_skills_dir / "alice" / "skills",
                 "alice-thing", layer="user")

    cfg = _make_cfg(agent_dir)
    prompt = await _prompt_str(cfg, request_user="alice")
    assert "Body von user: alice-thing" in prompt


async def test_user_skill_skipped_without_username(tmp_path, patch_user_skills_dir):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    _write_skill(patch_user_skills_dir / "alice" / "skills",
                 "alice-thing", layer="user")

    cfg = _make_cfg(agent_dir)
    prompt = await _prompt_str(cfg, request_user=None)
    assert "alice-thing" not in prompt


async def test_user_skill_skipped_for_internal_marker(tmp_path, patch_user_skills_dir):
    """Defense-in-Depth: selbst wenn jemand 'internal' durchreicht, kein Layer."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    _write_skill(patch_user_skills_dir / "internal" / "skills",
                 "internal-thing", layer="user")

    cfg = _make_cfg(agent_dir)
    prompt = await _prompt_str(cfg, request_user="internal")
    assert "internal-thing" not in prompt


async def test_agent_skill_shadows_user_skill(tmp_path, patch_user_skills_dir):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    _write_skill(agent_dir / "skills", "code-review",
                 layer="agent", prio=30)
    _write_skill(patch_user_skills_dir / "alice" / "skills",
                 "code-review", layer="user", prio=30)

    cfg = _make_cfg(agent_dir)
    prompt = await _prompt_str(cfg, request_user="alice")
    assert "Body von agent: code-review" in prompt
    assert "Body von user: code-review" not in prompt


async def test_invalid_request_user_skipped_safely(tmp_path, patch_user_skills_dir):
    """Ungültiger Username → kein Crash, kein Skill."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    cfg = _make_cfg(agent_dir)
    prompt = await _prompt_str(cfg, request_user="../evil")
    assert "Test-Agent" in prompt  # Identity da
    # kein Fehler, build_system_prompt kommt sauber durch
    assert isinstance(prompt, str)


# ────────────────────────────────────────────────────────────── Cross-User Cache-Regression

async def test_cross_user_skills_do_not_leak(tmp_path, patch_user_skills_dir):
    """Zwei aufeinanderfolgende build_system_prompt-Aufrufe für DEN SELBEN
    Agent mit unterschiedlichen request_user dürfen keine Skills vom
    vorigen User im Prompt tragen.

    Wenn dieser Test fehlschlägt, ist der static-Cache-Key zu grob — Fix:
    Cache-Key um request_user erweitern ODER Skills strikt in Dynamic
    halten. NICHT umgehen."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    _write_skill(patch_user_skills_dir / "alice" / "skills",
                 "alice-secret", layer="alice")
    _write_skill(patch_user_skills_dir / "bob" / "skills",
                 "bob-secret", layer="bob")

    cfg = _make_cfg(agent_dir)

    p_alice_1 = await _prompt_str(cfg, request_user="alice")
    p_bob     = await _prompt_str(cfg, request_user="bob")
    p_alice_2 = await _prompt_str(cfg, request_user="alice")

    assert "alice-secret" in p_alice_1
    assert "bob-secret"   not in p_alice_1

    assert "bob-secret"   in p_bob
    assert "alice-secret" not in p_bob

    assert "alice-secret" in p_alice_2
    assert "bob-secret"   not in p_alice_2


async def test_no_user_layer_without_request_user_even_with_user_dir(
        tmp_path, patch_user_skills_dir):
    """Klassischer Regression-Wächter: User-Skills existieren auf Platte,
    aber wenn request_user=None übergeben wird (ask_agent, internal cron),
    darf nichts davon auftauchen."""
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    _write_skill(patch_user_skills_dir / "alice" / "skills",
                 "alice-stuff", layer="alice")

    cfg = _make_cfg(agent_dir)
    prompt_auth = await _prompt_str(cfg, request_user="alice")
    prompt_anon = await _prompt_str(cfg, request_user=None)
    assert "alice-stuff" in prompt_auth
    assert "alice-stuff" not in prompt_anon


# ────────────────────────────────────────────────────────────── get_skill_tool_constraints

def test_get_skill_tool_constraints_uses_user_layer(tmp_path, patch_user_skills_dir):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    # User-Skill blockiert shell_exec
    _write_skill(patch_user_skills_dir / "alice" / "skills",
                 "block-shell", layer="user", template=SKILL_WITH_TOOLS)

    cfg = _make_cfg(agent_dir)

    allowed_a, blocked_a = get_skill_tool_constraints(
        cfg, "irgendein text", request_user="alice",
    )
    allowed_n, blocked_n = get_skill_tool_constraints(
        cfg, "irgendein text", request_user=None,
    )
    assert "shell_exec" in blocked_a
    assert blocked_n == []
