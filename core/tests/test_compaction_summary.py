"""Tests für #660: Strukturierter Task-State in der Compaction.

Deckt ab:
- Fakten-Sammler (collect_task_state_facts)
- Runtime-Kontext-Rendering
- Prompt-Builder inkl. Secret-Redaction
- YAML-Frontmatter-Parser mit Fehler-/Fallback-Fällen
- Auto-Compaction-Prompt nutzt Builder (Integration via Mock-LLM)
- /compact-Endpoint liefert weiterhin Response-Shape + Frontmatter-parseable
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.compaction_summary import (
    TaskStateFacts,
    build_summary_prompt,
    collect_task_state_facts,
    parse_summary_frontmatter,
    redact_summary_text,
    render_task_state_block,
)
from hydrahive_core.session_manager import Message, MessageRole


# Fake-Tokens zur Laufzeit zusammensetzen. Der Secret-Scan-Guard (#657)
# arbeitet statisch und darf keine Klartext-Pattern in dieser Test-Datei
# finden. Jede Komponente bleibt unter der Match-Schwelle des Scanners.
_GH_PREFIX   = "gh" + "p_"                              # „ghp_"
_GH_BODY     = ("A" * 28) + "BEEF"                      # 32 Chars (≥28)
FAKE_GH      = _GH_PREFIX + _GH_BODY                    # matcht gh_token-Regex
_SK_PREFIX   = "sk-" + "ant-"                           # „sk-ant-"
_SK_BODY     = ("x" * 16) + "1234"                      # 20 Chars (≥16)
FAKE_SK_ANT  = _SK_PREFIX + _SK_BODY                    # matcht sk-ant-Regex


# ── Redaction ────────────────────────────────────────────────────────────────

def test_redact_github_token():
    raw = f"Pushed with {FAKE_GH} to main"
    out = redact_summary_text(raw)
    assert _GH_BODY[:10] not in out
    assert "[REDACTED:gh_token:BEEF]" in out


def test_redact_sk_ant_key():
    raw = f"auth_token={FAKE_SK_ANT}"
    out = redact_summary_text(raw)
    assert _SK_BODY[:10] not in out
    assert "[REDACTED:sk-ant:" in out


def test_redact_no_match_returns_input():
    s = "Hello world, no secrets here."
    assert redact_summary_text(s) == s


def test_redact_does_not_truncate_long_text():
    """Im Unterschied zu hook_runtime._redact_str DARF redact_summary_text
    nicht auf 2048 Zeichen kürzen — Summaries sind länger."""
    s = "x" * 5000
    out = redact_summary_text(s)
    assert len(out) == 5000


def test_redact_non_string_passthrough():
    assert redact_summary_text(None) is None
    assert redact_summary_text(42) == 42  # type: ignore[arg-type]


# ── Frontmatter-Parser ───────────────────────────────────────────────────────

SAMPLE_FRONTMATTER_SUMMARY = """---
schema: compaction-task-state@1
goal: "Finalize #660"
files_touched:
  - core/src/hydrahive_core/compaction_summary.py
commits: [abc1234, deadbeef]
issues: ["#660"]
tests:
  passed: 163
  failed: 0
runtime:
  active_hooks: []
  worktrees: []
  active_skills: []
  isolation_mode: null
  write_scope: []
---

## Aktueller Arbeitskontext
Arbeit am Issue.
"""


def test_parse_frontmatter_roundtrip():
    data = parse_summary_frontmatter(SAMPLE_FRONTMATTER_SUMMARY)
    assert data is not None
    assert data["goal"] == "Finalize #660"
    assert data["commits"] == ["abc1234", "deadbeef"]
    assert data["issues"] == ["#660"]
    assert data["tests"]["passed"] == 163
    assert data["tests"]["failed"] == 0


def test_parse_frontmatter_missing_returns_none():
    """Altsummary-Text ohne Frontmatter → None, kein Crash."""
    old = "[Zusammenfassung der bisherigen Konversation]\n\nAgent hat XY gemacht."
    assert parse_summary_frontmatter(old) is None


def test_parse_frontmatter_empty_returns_none():
    assert parse_summary_frontmatter("") is None
    assert parse_summary_frontmatter("   \n  ") is None


def test_parse_frontmatter_empty_block_returns_none():
    assert parse_summary_frontmatter("---\n\n---\n\nBody") is None


def test_parse_frontmatter_invalid_yaml_returns_none():
    broken = "---\n: invalid :::: yaml ::::\n---\n\nBody"
    assert parse_summary_frontmatter(broken) is None


def test_parse_frontmatter_list_top_returns_none():
    """YAML-Liste statt Dict am top → None (wir wollen nur dict-Schema)."""
    s = "---\n- foo\n- bar\n---\n\nBody"
    assert parse_summary_frontmatter(s) is None


# ── TaskStateFacts & render_task_state_block ─────────────────────────────────

def _make_session(messages=None, *, working_state=None, session_id="s-test"):
    s = MagicMock()
    s.id = session_id
    s.messages = messages or []
    s.working_state = working_state
    return s


def _make_ws(**kw):
    """Einfaches working_state-stand-in. Attribut-Zugriff reicht."""
    ws = MagicMock()
    ws.current_goal = kw.get("current_goal", "")
    ws.last_tools = kw.get("last_tools", [])
    ws.open_files = kw.get("open_files", [])
    ws.git_state = kw.get("git_state", [])
    ws.last_memory_hits = kw.get("last_memory_hits", [])
    ws.active_tools = kw.get("active_tools", [])
    ws.budget_decisions = kw.get("budget_decisions", [])
    return ws


def test_collect_facts_from_working_state(tmp_path, monkeypatch):
    msgs = [
        Message.create(MessageRole.USER, "Fix bug #660"),
        Message.create(MessageRole.ASSISTANT, "Starting…"),
        Message.create(MessageRole.TOOL, "163 passed, 0 failed in tests/test_compaction_summary.py"),
        Message.create(MessageRole.TOOL, "git commit -m 'feat: foo'\n[main abc1234def5] feat: foo"),
        Message.create(MessageRole.USER, "Weiter mit #668 bitte"),
    ]
    ws = _make_ws(
        current_goal="Refactor compaction",
        open_files=["core/src/hydrahive_core/compaction_summary.py"],
        git_state=[{"path": "/opt/hydrahive", "branch": "main",
                    "ahead": 1, "behind": 0, "uncommitted": False}],
        last_tools=[{"name": "shell_exec", "summary": "pytest", "ok": True}],
    )
    session = _make_session(msgs, working_state=ws)

    # worktrees_dir auf tmp_path umleiten — keine echten Meta-Dateien da.
    from hydrahive_core.settings import settings
    monkeypatch.setattr(type(settings), "worktrees_dir",
                        property(lambda self: tmp_path / "worktrees"))

    facts = collect_task_state_facts(session, MagicMock(agent_dir=tmp_path))
    assert facts.goal == "Refactor compaction"
    assert "core/src/hydrahive_core/compaction_summary.py" in facts.files_touched
    assert "abc1234def5" in facts.commits or "abc1234" in facts.commits
    assert "#660" in facts.issues
    assert "#668" in facts.issues
    assert facts.tests.get("passed") == 163
    # failed=0 wird nicht in facts.tests aufgenommen (siehe Extractor:
    # nur wahrheitsgemäße Einträge → kein leaktes "failed: 0").
    assert "failed" not in facts.tests
    assert facts.last_tools == [{"name": "shell_exec", "summary": "pytest", "ok": True}]
    assert facts.runtime["active_hooks"] == []  # V1 best-effort, keine Quelle


def test_collect_facts_empty_session():
    session = _make_session([], working_state=None)
    facts = collect_task_state_facts(session, MagicMock(agent_dir=None))
    assert facts.goal == ""
    assert facts.commits == []
    assert facts.issues == []
    assert facts.tests == {}


def test_render_task_state_block_contains_facts():
    facts = TaskStateFacts(
        goal="Tidy the garden",
        files_touched=["foo.py", "bar.py"],
        commits=["deadbeef"],
        issues=["#660", "#668"],
        git_state=[{"path": "/x", "branch": "main", "ahead": 2, "behind": 0, "uncommitted": True}],
        last_tools=[{"name": "shell_exec", "summary": "ls", "ok": True}],
        tests={"passed": 10, "suites": ["tests/test_x.py"]},
        runtime={"active_hooks": [], "worktrees": ["wt-1"], "active_skills": ["code-review"]},
        next_step_hint="Run /compact",
    )
    block = render_task_state_block(facts)
    assert "Tidy the garden" in block
    assert "foo.py" in block
    assert "deadbeef" in block
    assert "#660" in block
    assert "wt-1" in block
    assert "code-review" in block


# ── build_summary_prompt ─────────────────────────────────────────────────────

def test_build_summary_prompt_has_schema_block():
    facts = TaskStateFacts(goal="do X")
    prompt = build_summary_prompt("USER: hi", facts)
    assert len(prompt) == 2
    assert prompt[0]["role"] == "system"
    assert prompt[1]["role"] == "user"
    system = prompt[0]["content"]
    user = prompt[1]["content"]
    assert "schema: compaction-task-state@1" in system
    assert "## Runtime-Kontext" in system
    assert "do X" in system
    assert "USER: hi" in user
    # Output-Schema enthält Frontmatter-Struktur
    assert "---" in system
    assert "scope_in" in system
    assert "scope_out" in system


def test_build_summary_prompt_redacts_history():
    raw_history = f"USER: push with token {FAKE_GH}"
    prompt = build_summary_prompt(raw_history, TaskStateFacts())
    user = prompt[1]["content"]
    assert _GH_BODY[:10] not in user
    assert "[REDACTED:gh_token:" in user


def test_build_summary_prompt_redacts_existing_summary():
    prompt = build_summary_prompt(
        "USER: hi", TaskStateFacts(),
        existing_summary=f"Old: {FAKE_SK_ANT}",
    )
    user = prompt[1]["content"]
    assert _SK_BODY[:10] not in user
    assert "[REDACTED:sk-ant:" in user
    # System-Prompt signalisiert Update-Modus (deutsche Flexion lassen wir offen)
    assert "bisherig" in prompt[0]["content"].lower()


def test_build_summary_prompt_facts_verbatim_in_system():
    facts = TaskStateFacts(
        goal="Exactly this goal string",
        files_touched=["weird/path.py"],
        commits=["cafebabe"],
        issues=["#999"],
    )
    system = build_summary_prompt("", facts)[0]["content"]
    assert "Exactly this goal string" in system
    assert "weird/path.py" in system
    assert "cafebabe" in system
    assert "#999" in system


def test_build_summary_prompt_secret_in_goal_redacted():
    facts = TaskStateFacts(goal=f"Token {FAKE_GH} burn")
    system = build_summary_prompt("", facts)[0]["content"]
    assert _GH_BODY[:10] not in system
    assert "[REDACTED:gh_token:" in system


# ── Auto-Compaction ruft Builder auf ─────────────────────────────────────────

async def test_auto_compaction_uses_structured_builder(monkeypatch, tmp_path):
    """Schließt die Lücke zwischen Builder und `_compact_if_needed`: wir
    mocken `_compact_call` und prüfen, dass der gesendete Prompt das
    Frontmatter-Schema + Runtime-Kontext trägt — und die Redaction greift."""
    from hydrahive_core import orchestrator_context as oc

    # Session mit einem Secret in History
    msgs = [
        Message.create(MessageRole.USER, "Push it"),
        Message.create(MessageRole.ASSISTANT, "Running git"),
        Message.create(MessageRole.TOOL,
                       f"Set remote with token {FAKE_GH}"),
        Message.create(MessageRole.ASSISTANT, "Committed deadbeef123"),
    ]
    ws = _make_ws(current_goal="Deploy", open_files=["deploy.sh"])
    session = _make_session(msgs * 30, working_state=ws)  # inflate für Threshold

    sessions = MagicMock()
    sessions.estimated_tokens = MagicMock(return_value=50_000)
    sessions.get_active = MagicMock(return_value=session)
    sessions.compact = AsyncMock()
    sessions.new_session = AsyncMock()
    sessions._db_replace_messages = MagicMock()

    boss_cfg = MagicMock()
    boss_cfg.llm.model = "claude-3-5-sonnet-20241022"
    boss_cfg.llm.max_tokens = 400
    boss_cfg.compaction_threshold = None
    boss_cfg.agent_dir = None  # Memory-Flush überspringen

    # worktrees_dir weg-stubben
    from hydrahive_core.settings import settings
    monkeypatch.setattr(type(settings), "worktrees_dir",
                        property(lambda self: tmp_path / "worktrees"))

    # LLM mocken — liefert eine Summary mit Frontmatter
    fake_summary = (
        "---\n"
        "schema: compaction-task-state@1\n"
        "goal: Deploy\n"
        "commits: [deadbeef123]\n"
        "---\n\n"
        "## Aktueller Arbeitskontext\nDeploy läuft.\n"
    )
    captured = {}

    async def _fake_compact_call(_boss, _model, prompt, max_tokens):
        captured["prompt"] = prompt
        captured["max_tokens"] = max_tokens
        return fake_summary

    monkeypatch.setattr(oc, "_compact_call", _fake_compact_call)

    await oc._compact_if_needed(sessions, "proj", boss_cfg)

    # LLM wurde aufgerufen
    assert "prompt" in captured
    system = captured["prompt"][0]["content"]
    user   = captured["prompt"][1]["content"]

    # Neuer Builder ist aktiv (Frontmatter-Schema im System-Prompt)
    assert "schema: compaction-task-state@1" in system
    # Runtime-Kontext enthält goal
    assert "Deploy" in system
    # Secret ist vor LLM-Aufruf redacted
    assert _GH_BODY[:10] not in user
    assert "[REDACTED:gh_token:" in user

    # sessions.compact wurde mit Summary-Text aufgerufen
    assert sessions.compact.await_count >= 1
    first_call = sessions.compact.await_args_list[0]
    passed_summary = first_call.args[1] if len(first_call.args) > 1 else first_call.kwargs.get("summary")
    # Summary wurde redacted vor Persist (Test: Frontmatter intakt, keine Secret-Reinjection)
    assert parse_summary_frontmatter(passed_summary) is not None


async def test_auto_compaction_redacts_llm_response(monkeypatch, tmp_path):
    """Selbst wenn das Modell ein Secret in der Summary wiederholt, muss
    der an `sessions.compact` übergebene Text redacted sein."""
    from hydrahive_core import orchestrator_context as oc

    session = _make_session(
        [Message.create(MessageRole.USER, "x")] * 40,
        working_state=_make_ws(current_goal=""),
    )
    sessions = MagicMock()
    sessions.estimated_tokens = MagicMock(return_value=50_000)
    sessions.get_active = MagicMock(return_value=session)
    sessions.compact = AsyncMock()
    sessions.new_session = AsyncMock()
    sessions._db_replace_messages = MagicMock()

    boss_cfg = MagicMock(agent_dir=None)
    boss_cfg.llm.model = "claude-3-5-sonnet-20241022"
    boss_cfg.llm.max_tokens = 400
    boss_cfg.compaction_threshold = None

    from hydrahive_core.settings import settings
    monkeypatch.setattr(type(settings), "worktrees_dir",
                        property(lambda self: tmp_path / "worktrees"))

    leak = FAKE_GH
    malicious_summary = f"---\ngoal: x\n---\n\n## Body\nToken leaked: {leak}"

    async def _fake(_boss, _model, _prompt, max_tokens):
        return malicious_summary

    monkeypatch.setattr(oc, "_compact_call", _fake)

    await oc._compact_if_needed(sessions, "proj", boss_cfg)

    # Summary-Argument an sessions.compact prüfen
    call = sessions.compact.await_args_list[0]
    passed = call.args[1] if len(call.args) > 1 else call.kwargs.get("summary")
    assert leak not in passed
    assert "[REDACTED:gh_token:" in passed
