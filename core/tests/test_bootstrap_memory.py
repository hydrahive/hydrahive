"""
test_bootstrap_memory.py — #714 (#708-F)

Verifiziert:
- Bootstrap legt **kein** `feedback_*.md` oder Core-Regel-Duplikat im Memory an
- `MEMORY.md`-Stub enthält den kurzen Hinweis auf die Core-Policy (#714)
- Bestehende `MEMORY.md` wird **nicht** überschrieben
- `project_structure.md` wird bei jedem nicht-skipped Run geschrieben (bestehendes
  Verhalten — Regression absichern)

Der Bootstrap wird hier asynchron mit einem tmp-Projekt-Dir aufgerufen. Das `files/`-
Unterverzeichnis bleibt leer; `_scan_repo_structure` liefert dann einen Fallback-
String — für die Tests irrelevant.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.bootstrap_memory import bootstrap_project_memory, is_bootstrap_done
from hydrahive_core.memory_paths import MEMORY_INDEX_FILENAME


def _run(coro):
    return asyncio.run(coro)


def _new_project(tmp_path: Path, pid: str = "test-bootstrap") -> Path:
    p = tmp_path / pid
    (p / "files").mkdir(parents=True)
    return p


# ── Inhalt: kein Core-Regel-Duplikat ───────────────────────────────────────

def test_bootstrap_does_not_write_core_policy_feedback_files(tmp_path):
    """Bootstrap darf keine `feedback_*.md` oder andere Core-Regel-Dateien anlegen —
    Core-Regeln kommen aus der Core-Policy im System-Prompt."""
    pid = "proj-no-feedback"
    project_dir = _new_project(tmp_path, pid)

    result = _run(bootstrap_project_memory(pid, project_dir))
    assert result["ok"], f"Bootstrap fehlgeschlagen: {result}"

    memory_dir = project_dir / "memory"
    feedback_files = list(memory_dir.glob("feedback_*.md"))
    assert not feedback_files, (
        f"Bootstrap hat feedback_*.md angelegt — Core-Regel-Duplikate sind "
        f"verboten (#714). Gefunden: {[f.name for f in feedback_files]}"
    )

    # Auch nichts mit 'token_discipline' oder 'policy' im Namen
    policy_like = [
        f for f in memory_dir.iterdir()
        if any(tok in f.name.lower() for tok in ("token_discipline", "policy"))
    ]
    assert not policy_like, (
        f"Bootstrap legte Policy-ähnliche Memory-Datei(en) an: "
        f"{[f.name for f in policy_like]}"
    )


# ── MEMORY.md-Stub enthält Core-Policy-Hinweis ─────────────────────────────

def test_memory_stub_references_core_policy(tmp_path):
    pid = "proj-stub-hint"
    project_dir = _new_project(tmp_path, pid)

    _run(bootstrap_project_memory(pid, project_dir))

    stub = (project_dir / "memory" / MEMORY_INDEX_FILENAME).read_text(encoding="utf-8")
    assert "Core-Policy" in stub, (
        "MEMORY.md-Stub muss auf die Core-Policy verweisen (#714), damit klar ist, "
        "dass Core-Regeln aus dem System-Prompt kommen"
    )
    assert "System-Prompt" in stub, "Stub-Hinweis soll den System-Prompt namentlich nennen"
    # Hinweis bleibt knapp — Stub insgesamt ≤ 1 kB
    assert len(stub) < 1000, (
        f"Stub ist zu lang ({len(stub)} B) — MEMORY.md wird bei jedem Turn gerendert"
    )


# ── Idempotenz: bestehende MEMORY.md nicht überschreiben ───────────────────

def test_existing_memory_md_is_not_overwritten(tmp_path):
    pid = "proj-preserve-index"
    project_dir = _new_project(tmp_path, pid)
    memory_dir = project_dir / "memory"
    memory_dir.mkdir(exist_ok=True)
    admin_content = "# Admin-kuratierter Index\n\n- [notes.md](notes.md) — User Notes\n"
    (memory_dir / MEMORY_INDEX_FILENAME).write_text(admin_content, encoding="utf-8")

    _run(bootstrap_project_memory(pid, project_dir))

    now = (memory_dir / MEMORY_INDEX_FILENAME).read_text(encoding="utf-8")
    assert now == admin_content, (
        "Bestehende MEMORY.md wurde beim Bootstrap überschrieben — "
        "Admin-Kuratierung muss erhalten bleiben"
    )


# ── Regression: project_structure.md wird immer geschrieben ─────────────────

def test_project_structure_md_is_always_written_on_first_run(tmp_path):
    pid = "proj-structure"
    project_dir = _new_project(tmp_path, pid)

    result = _run(bootstrap_project_memory(pid, project_dir))
    assert result["ok"]
    assert "project_structure.md" in result.get("files_written", []), (
        f"project_structure.md sollte beim ersten Run geschrieben werden: {result}"
    )
    assert (project_dir / "memory" / "project_structure.md").exists()


# ── Sentinel / skip-Verhalten bleibt unverändert ────────────────────────────

def test_second_run_skips_via_sentinel(tmp_path):
    pid = "proj-sentinel"
    project_dir = _new_project(tmp_path, pid)

    r1 = _run(bootstrap_project_memory(pid, project_dir))
    assert r1["ok"] and not r1.get("skipped")
    assert is_bootstrap_done(project_dir)

    r2 = _run(bootstrap_project_memory(pid, project_dir))
    assert r2["ok"] and r2.get("skipped"), (
        f"Zweiter Run ohne force=true sollte geskippt werden: {r2}"
    )


# ── Force-Rerun schreibt wieder, überschreibt aber MEMORY.md nicht ─────────

def test_force_rerun_rewrites_structure_but_keeps_memory_md(tmp_path):
    pid = "proj-force"
    project_dir = _new_project(tmp_path, pid)

    _run(bootstrap_project_memory(pid, project_dir))

    # User bearbeitet MEMORY.md
    custom = "# Mein Index\n"
    (project_dir / "memory" / MEMORY_INDEX_FILENAME).write_text(custom, encoding="utf-8")

    r = _run(bootstrap_project_memory(pid, project_dir, force=True))
    assert r["ok"] and not r.get("skipped")
    assert "project_structure.md" in r.get("files_written", []), (
        "Force-Rerun soll project_structure.md neu schreiben"
    )
    # MEMORY.md bleibt unverändert — Admin-Kuration über alles
    assert (project_dir / "memory" / MEMORY_INDEX_FILENAME).read_text(encoding="utf-8") == custom
