"""
Invariante #772: persona_file() wählt die effektiv wirksame Persona-Datei.

Der orchestrator_context-Loader priorisiert AGENT.md (v2) vor soul.md (v1).
Editor/API/Export-Code müssen über persona_file() gehen, damit der User
genau die Datei editiert die der Agent auch wirklich liest. Ohne das
driftet UI-Inhalt stumm vom LLM-Input ab.

Deckt: Helper-Semantik (Read-Priorität, Write-Default) + die 5 Call-Sites
(Soul-GET, Agent-Update, Personal-Agent-Persist, Template-Preview, Export).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hydrahive_core.agent_config import persona_file


def test_persona_file_prefers_agent_md(tmp_path: Path):
    (tmp_path / "soul.md").write_text("v1 soul content")
    (tmp_path / "AGENT.md").write_text("v2 agent content")
    assert persona_file(tmp_path).name == "AGENT.md"


def test_persona_file_falls_back_to_soul_md(tmp_path: Path):
    (tmp_path / "soul.md").write_text("only soul")
    assert persona_file(tmp_path).name == "soul.md"


def test_persona_file_picks_agent_md_when_only_agent_md(tmp_path: Path):
    (tmp_path / "AGENT.md").write_text("only agent")
    assert persona_file(tmp_path).name == "AGENT.md"


def test_persona_file_default_write_target_on_empty_dir(tmp_path: Path):
    """Frischer Agent: noch keine Persona-Datei — Default zum Schreiben ist soul.md."""
    target = persona_file(tmp_path)
    assert target.name == "soul.md"
    assert not target.exists()


def test_write_roundtrip_updates_agent_md_when_present(tmp_path: Path):
    """Edit-Szenario: AGENT.md existiert → Schreiben landet in AGENT.md, soul.md bleibt."""
    (tmp_path / "soul.md").write_text("old v1 content")
    (tmp_path / "AGENT.md").write_text("old v2 content")

    persona_file(tmp_path).write_text("new persona text")

    assert (tmp_path / "AGENT.md").read_text() == "new persona text"
    assert (tmp_path / "soul.md").read_text() == "old v1 content"
