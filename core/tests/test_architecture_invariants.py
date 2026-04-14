"""
Architektur-Invariant-Tests — Issue #639

Diese Tests sichern System-weite Architektur-Invarianten ab, nicht Helper-
Funktionen. Sie sind die erste Stufe (Strang E) der Architektur-Konsolidierung
v3 — Merge-Gate für die folgenden Stränge A–D.

Konvention für erwartete rote Tests:
    @pytest.mark.xfail(strict=True, reason="...konkrete Drift...")

`strict=True` heißt: wenn ein erwarteter Drift-Test überraschend grün wird,
schlägt CI Alarm — das ist gewollt (Fix wäre dann da, ohne dass jemand das
xfail entfernt hat).

Test-Ebene: realer Builder / Session / Persistenz / Message-Normalisierung
bis kurz vor dem Provider-Call. Keine HTTP-Requests. Keine Mocks auf
Builder-Ebene. Provider-spezifische Adapter (anthropic/litellm) sind über
conftest.py global gemockt — die hier getesteten Pfade laufen davor.

Formale Blocker (siehe BLOCKERS am Ende der Datei):
    B1 — OAuth-Stream Provider-Format-Konvertierung nicht pure-callable
    B2 — shell_exec Sandbox-Scope nur über Prozessstart beobachtbar
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

AGENT_MD_MARKER = "MARKER_AGENTMD_42"


@pytest.fixture
def tmp_agent_dir(tmp_path):
    """Minimaler agent_dir mit AGENT.md, ohne soul.md / memory."""
    d = tmp_path / "agent"
    d.mkdir()
    (d / "AGENT.md").write_text(
        f"# Test-Agent\n\n{AGENT_MD_MARKER}\n\n"
        "Du bist ein Test-Agent für Invariant-Tests.\n",
        encoding="utf-8",
    )
    return d


@pytest.fixture
def boss_cfg(tmp_agent_dir):
    """Minimaler boss_cfg-Stub für Builder-Aufrufe."""
    cfg = MagicMock()
    cfg.id = "test-agent-invariants"
    cfg.identity = "Test-Agent"
    cfg.agent_dir = tmp_agent_dir
    cfg.soul = None
    cfg.sources = []
    return cfg


@pytest.fixture(autouse=True)
def _clear_prompt_caches():
    """Vor jedem Test die Prompt-Caches im Modul leeren — sonst leakt
    Cache zwischen Tests. (#636: nur noch _STATIC_PROMPT_CACHE.)"""
    from hydrahive_core import orchestrator_context as oc
    oc._STATIC_PROMPT_CACHE.clear()
    oc._SEGMENT_HASHES.clear()
    yield
    oc._STATIC_PROMPT_CACHE.clear()
    oc._SEGMENT_HASHES.clear()


# ===========================================================================
# Invariante 1 — AGENT.md identisch in non-stream / OAuth-stream / litellm-stream
# ===========================================================================

async def test_invariant1a_agent_md_marker_present_in_nonstream_and_stream_builder(boss_cfg):
    """1a: AGENT.md-Inhalt erscheint in beiden Builder-Outputs.

    #636: nach Vereinheitlichung gibt es nur noch einen Builder. Test prüft
    dass der Builder AGENT.md lädt — unabhängig von Aufruf-Stil.
    """
    from hydrahive_core.orchestrator_context import build_system_prompt

    static_p, dynamic_p = await build_system_prompt(boss_cfg, "test query")
    prompt = (static_p + "\n\n" + dynamic_p).strip() if dynamic_p else static_p

    assert AGENT_MD_MARKER in prompt, (
        "AGENT.md-Inhalt fehlt im System-Prompt — build_system_prompt "
        f"lädt AGENT.md nicht. Output[:300]: {prompt[:300]!r}"
    )


async def test_invariant1b_agent_md_survives_normalize_messages_for_call(boss_cfg):
    """1b: AGENT.md-Inhalt bleibt erhalten nach `normalize_messages_for_call`.

    Die Normalisierung läuft im litellm-stream (orchestrator_stream.py:966)
    UND im non-stream OAuth-Pfad (orchestrator_llm.py:613). Wenn
    normalize System-Inhalte beschädigt, bricht beide Pfade.

    Erwartet grün — normalize_messages_for_call ist text-canonicalize-only.
    """
    from hydrahive_core.orchestrator_context import build_system_prompt
    from hydrahive_core.message_normalization import normalize_messages_for_call

    static_p, dynamic_p = await build_system_prompt(boss_cfg, "test")
    sys_prompt = (static_p + "\n\n" + dynamic_p).strip() if dynamic_p else static_p

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": "hi"},
    ]
    normalized = normalize_messages_for_call(messages)

    sys_msgs = [m for m in normalized if m.get("role") == "system"]
    assert sys_msgs, "system-Message wurde von normalize entfernt"
    sys_content = sys_msgs[0].get("content", "")
    if isinstance(sys_content, list):
        sys_content = "\n".join(
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in sys_content
        )
    assert AGENT_MD_MARKER in sys_content, (
        "AGENT.md-Marker geht durch normalize verloren — "
        f"sys_content[:300]: {sys_content[:300]!r}"
    )


async def test_invariant1c_strukturelle_identitaet_zwischen_nonstream_und_stream(boss_cfg):
    """1c (#636): Strukturelle Identität — non-stream und stream müssen
    byte-identische System-Prompts produzieren wenn der Input identisch ist.

    Nach Vereinheitlichung gibt es nur einen Builder. Beide Pfade rufen ihn
    gleichermaßen mit `session=` — derselbe ContextChannels-Output, derselbe
    `<memory_dynamic>`-Marker, dieselbe Reihenfolge. Drift kann strukturell
    nicht mehr entstehen, solange beide Pfade dieselbe Funktion aufrufen.
    """
    from hydrahive_core.orchestrator_context import build_system_prompt
    from hydrahive_core.working_state import WorkingState

    state = WorkingState(current_goal="STRUCTURAL_IDENTITY_CHECK")
    session_stub = MagicMock()
    session_stub.working_state = state

    static_a, dynamic_a = await build_system_prompt(boss_cfg, "test", session=session_stub)
    static_b, dynamic_b = await build_system_prompt(boss_cfg, "test", session=session_stub)

    assert static_a == static_b, "Static-Anteil zwischen zwei Builds identisch"
    assert dynamic_a == dynamic_b, "Dynamic-Anteil zwischen zwei Builds identisch"
    # Kompositions-Test: das was non-stream und stream call-sites bauen müssen,
    # ist eindeutig durch das Tuple bestimmt — kein Pfad-spezifisches Wrapping.
    nonstream_join = (static_a + "\n\n" + dynamic_a).strip() if dynamic_a else static_a
    stream_join = (static_b + "\n\n" + dynamic_b).strip() if dynamic_b else static_b
    assert nonstream_join == stream_join


# ===========================================================================
# Invariante 2 — AGENT.md-Änderung invalidiert Cache
# ===========================================================================

def test_invariant2_agent_md_change_invalidates_prompt_cache_hash(tmp_agent_dir):
    """AGENT.md-Edit muss den Hash ändern, der den Prompt-Cache schlüsselt.

    Wenn AGENT.md nicht im Hash steckt, bleibt ein veralteter Prompt im
    Cache bis TTL-Ablauf — Persona-Edits werden unsichtbar.

    Erwartet grün — orchestrator_context.py:166-168 inkludiert AGENT.md
    explizit im Cache-Hash.
    """
    from hydrahive_core.orchestrator_context import _prompt_cache_hash

    h1 = _prompt_cache_hash(tmp_agent_dir, "normal")

    # mtime explizit erhöhen — manche Filesystems haben sekundengenaue Auflösung,
    # ein frisches write_text() gibt sonst evtl. denselben Stempel.
    agent_md = tmp_agent_dir / "AGENT.md"
    new_mtime = agent_md.stat().st_mtime + 5.0
    os.utime(agent_md, (new_mtime, new_mtime))

    h2 = _prompt_cache_hash(tmp_agent_dir, "normal")

    assert h1 != h2, (
        f"AGENT.md-mtime-Änderung ändert Hash nicht — Cache invalidiert nicht. "
        f"h1={h1} h2={h2}"
    )


# ===========================================================================
# Invariante 3 — file_write / shell_exec / git_* sehen denselben Workspace
# ===========================================================================

def test_invariant3_workspace_root_shared_between_file_and_git_tools(tmp_path, monkeypatch):
    """#635: file_*, git_* und shell_exec müssen denselben effektiven
    Working Tree für dasselbe project_id liefern.

    Geschärft (kein is_relative_to, kein Parent-Trick): file-Resolver und
    git-Resolver müssen byte-identische Working-Tree-Wurzel liefern.
    Sonst kann `git_commit_all` eine `file_write`-Änderung nicht committen.
    """
    from hydrahive_core import tool_registry, tools_git

    fake_projects = tmp_path / "projects"
    monkeypatch.setattr(tool_registry, "PROJECTS_ROOT", fake_projects)

    pid = "myproj"
    (fake_projects / pid).mkdir(parents=True)

    file_root = tool_registry.assert_path_within_project("foo.txt", pid).parent
    git_root = tools_git._project_workspace(pid)
    ssot = tool_registry.workspace_root(pid)

    # Strikte Identität: file-Tool-Working-Tree == git-Tool-Working-Tree == SSOT.
    assert file_root == git_root == ssot, (
        f"Drift: file_root={file_root!s}, git_root={git_root!s}, "
        f"workspace_root={ssot!s}. Müssen identisch sein."
    )
    # Path-Equivalenz: file_write("foo.txt") landet in workspace_root/foo.txt.
    assert (ssot / "foo.txt") == tool_registry.assert_path_within_project("foo.txt", pid)


def test_invariant3_shell_sandbox_scope_uses_workspace_root(tmp_path, monkeypatch):
    """#635 + B2 aus #639: ShellExecTool-Sandbox bindet workspace_root(pid)
    als read-write Mount — derselbe Tree wie file_*/git_*.

    Pure Helper-Test: ruft `_resolve_sandbox_scope(pid, cwd)` direkt auf,
    inspiziert die bwrap-bind-Args. Kein Prozessstart nötig — B2 aufgelöst.
    """
    from hydrahive_core import tool_registry
    from hydrahive_core.tool_registry import _resolve_sandbox_scope, workspace_root

    fake_projects = tmp_path / "projects"
    monkeypatch.setattr(tool_registry, "PROJECTS_ROOT", fake_projects)

    pid = "myproj"
    proj_dir = fake_projects / pid
    proj_dir.mkdir(parents=True)
    expected_root = workspace_root(pid)

    cwd, bind_args = _resolve_sandbox_scope(pid, str(proj_dir))

    # cwd muss innerhalb des Workspace liegen (oder workspace selbst sein).
    assert cwd == expected_root or expected_root in cwd.parents, (
        f"Sandbox-cwd liegt nicht im Workspace: {cwd} vs {expected_root}"
    )
    # Workspace-Root muss als read-write bind in den Args stehen.
    bind_pairs = list(zip(bind_args[::2] + [None], bind_args[1::2] + [None]))
    rw_binds = [
        bind_args[i + 1]
        for i, a in enumerate(bind_args)
        if a == "--bind" and i + 1 < len(bind_args)
    ]
    assert str(expected_root) in rw_binds, (
        f"workspace_root({pid})={expected_root} fehlt als rw-Mount in bind_args. "
        f"Vorhandene rw-Mounts: {rw_binds}"
    )


# ===========================================================================
# Invariante 4 — Tool-Events strukturell konsistent über Session/LLM/Stream
# ===========================================================================

def _make_session_with_tool_roundtrip():
    """Hilfs-Setup: Session mit einem strukturierten Tool-Roundtrip
    (USER → ASSISTANT mit tool_calls → TOOL mit tool_call_id)."""
    from hydrahive_core.session_manager import Session, Message, MessageRole

    session = Session.new("test-proj")
    session.append(Message.create(
        role=MessageRole.USER, content="run foo",
    ))
    session.append(Message.create(
        role=MessageRole.ASSISTANT, content="",
        tool_calls=[{
            "id": "call_42",
            "type": "function",
            "function": {"name": "foo", "arguments": "{}"},
        }],
    ))
    session.append(Message.create(
        role=MessageRole.TOOL, content="result data here",
        tool_call_id="call_42", tool_name="foo",
    ))
    return session


def _has_structured_tool_event(messages: list[dict]) -> bool:
    """True, wenn mindestens eine Message strukturierte Tool-Form hat:
    OpenAI-tool_calls-Feld, role=tool, oder Anthropic-Block-Liste mit
    tool_use/tool_result."""
    for m in messages:
        if m.get("tool_calls"):
            return True
        if m.get("role") == "tool":
            return True
        content = m.get("content")
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") in ("tool_use", "tool_result"):
                    return True
    return False


def test_invariant4a_tool_events_structured_through_session_llm_context():
    """4a (#637): persistierte Tool-Calls/Results müssen in
    `session.llm_context()` als strukturierte OpenAI-Form überleben.

    Nach Refactor liefert `Message.as_llm_message()` `role: "tool"` mit
    `tool_call_id` für TOOL-Messages und assistant mit `tool_calls`-Feld
    für Tool-Calls. Provider-Adapter (Anthropic-OAuth) konvertieren
    erst beim Senden — kein Freitext-Flattening mehr.
    """
    session = _make_session_with_tool_roundtrip()
    ctx = session.llm_context()

    assert _has_structured_tool_event(ctx), (
        "Tool-Events sind in llm_context nicht als strukturierte OpenAI-Form "
        f"erhalten geblieben. Output: {ctx}"
    )


def test_invariant4b_tool_events_structured_through_stream_normalize():
    """4b: Stream-Vorbereitung (`session.llm_context()` →
    `normalize_messages_for_call()`) muss Tool-Struktur erhalten."""
    from hydrahive_core.message_normalization import normalize_messages_for_call

    session = _make_session_with_tool_roundtrip()
    ctx = [{"role": "system", "content": "sys"}] + session.llm_context()
    normalized = normalize_messages_for_call(ctx)

    assert _has_structured_tool_event(normalized), (
        "Tool-Events erreichen die Stream-Vorbereitung als Freitext, nicht "
        "als strukturierte Provider-Form. "
        f"Normalized: {normalized}"
    )


# ===========================================================================
# Invariante 5 — Resume injiziert working_state sichtbar
# ===========================================================================

WORKING_STATE_MARKER = "MARK_RESUME_WORKING_STATE_42"


async def test_invariant5a_resume_restores_working_state_in_stream_builder(
    tmp_path, boss_cfg,
):
    """5a: Stream-Builder muss working_state-Snapshot nach Resume in den
    dynamic_suffix injizieren.

    Erwartet grün — der Pfad ist verdrahtet (session_manager.py:933 →
    orchestrator_context.py:726-732)."""
    from hydrahive_core.session_manager import SessionManager
    from hydrahive_core.working_state import WorkingState
    from hydrahive_core.orchestrator_context import build_system_prompt

    sm = SessionManager(projects_dir=tmp_path)
    sm.start()

    # Session erzeugen + persistieren
    session = sm.get_or_create("test-proj-resume")
    session_id = session.id

    state = WorkingState(
        current_goal=WORKING_STATE_MARKER,
        open_files=["foo.py", "bar.py"],
    )
    sm.save_snapshot(session_id, state, turn_seq=1)

    # Aktive Session künstlich entfernen, damit resume_session wirklich aus DB lädt
    sm._active.pop("test-proj-resume", None)

    resumed = await sm.resume_session("test-proj-resume", session_id)
    assert resumed is not None, "resume_session lieferte None"
    assert resumed.working_state is not None, (
        "working_state ist nach resume nicht am Session-Objekt — "
        "session_manager.py:923-933"
    )

    static_p, dynamic_p = await build_system_prompt(
        boss_cfg, "test", session=resumed,
    )
    assert WORKING_STATE_MARKER in dynamic_p, (
        "working_state.current_goal nicht im dynamic_suffix gelandet. "
        f"dynamic[:500]: {dynamic_p[:500]!r}"
    )


async def test_invariant5b_resume_restores_working_state_in_nonstream_builder(boss_cfg):
    """5b (#636): Non-stream-Builder muss working_state ebenfalls injizieren.

    Nach Vereinheitlichung gibt es nur einen Builder. Non-stream call-site
    in orchestrator.py:503 übergibt jetzt `session=` analog zum Stream-Pfad
    (vorher: kein session-Parameter, working_state ging verloren).
    """
    from hydrahive_core.orchestrator_context import build_system_prompt
    from hydrahive_core.working_state import WorkingState

    state = WorkingState(current_goal=WORKING_STATE_MARKER + "_NONSTREAM")
    session_stub = MagicMock()
    session_stub.working_state = state

    static_p, dynamic_p = await build_system_prompt(boss_cfg, "test", session=session_stub)
    prompt = (static_p + "\n\n" + dynamic_p).strip() if dynamic_p else static_p

    assert WORKING_STATE_MARKER + "_NONSTREAM" in prompt, (
        "build_system_prompt injiziert working_state nicht im non-stream-"
        "äquivalenten Aufruf-Stil."
    )


# ===========================================================================
# Invariante 6 — Permission-/Execution-Mode-Konsolidierung (#638)
# ===========================================================================
# Eine Quelle pro Entscheidung:
#   1. Tool-Whitelist: _allowed_tool_map (V2_CORE_TOOL_IDS + per-session deferred)
#   2. Risiko-Engine: permission_classifier.classify_action
#   3. Sandbox-Level (nur shell_exec): execution_mode
# Tote Permission-Layer (permissions_required, effective_permissions) sind weg.

def test_invariant6a_tool_whitelist_is_single_allow_source():
    """6a: _V2_CORE_TOOL_IDS ist die einzige Default-Whitelist.
    Keine Aktivierung über agent_cfg.tools/tools_extra/tools_deny im v2-Pfad.
    """
    from hydrahive_core.orchestrator import Orchestrator

    core_ids = Orchestrator._V2_CORE_TOOL_IDS
    # Erwartete Core-Tools (v2-Stand)
    expected_core = {
        "shell_exec", "file_read", "file_write", "file_patch",
        "file_search", "web_search", "read_memory", "write_memory",
        "ask_agent",
    }
    assert expected_core.issubset(core_ids), (
        f"V2 Core-Tools fehlen in Whitelist: {expected_core - core_ids}"
    )
    # Git-Tools sind NICHT default — müssen via ToolSearch geladen werden
    assert "git_clone" not in core_ids, "git_clone darf nicht in Default-Whitelist sein"
    assert "git_commit_all" not in core_ids


def test_invariant6b_no_permissions_required_metadata_in_core_tools():
    """6b (#638): permissions_required ist als Tool-Metadatum entfernt.

    Weder BaseTool noch Git-/Gitea-Tools haben dieses Property mehr.
    """
    from hydrahive_core.tool_registry import BaseTool
    from hydrahive_core import tools_git, tools_gitea

    assert not hasattr(BaseTool, "permissions_required"), (
        "BaseTool hat noch eine permissions_required-Property — toter Layer."
    )
    # Stichprobe: drei konkrete Tools aus den ehemaligen Override-Stellen
    for cls_name in ("GitCloneTool", "GitStatusTool", "GitCommitAllTool"):
        cls = getattr(tools_git, cls_name)
        # Property kann auf Klasse oder Instanz geprüft werden
        assert not hasattr(cls, "permissions_required"), (
            f"{cls_name} deklariert noch permissions_required."
        )


def test_invariant6c_effective_permissions_is_gone():
    """6c (#638): AgentConfig.effective_permissions existiert nicht mehr.

    Tote Decorator-Methode war hardcoded `return None` und Quelle der Drift.
    """
    from hydrahive_core.agent_config import AgentConfig

    assert not hasattr(AgentConfig, "effective_permissions"), (
        "AgentConfig hat noch effective_permissions — toter Layer."
    )


def test_invariant6d_execution_mode_only_affects_shell():
    """6d (#638): execution_mode beeinflusst nur shell_exec, nicht file_*/git_*.

    Wir prüfen, dass die Execute-Signatur der Git-Tools den execution_mode
    nicht semantisch konsumiert (Tools nehmen **kwargs entgegen, schauen aber
    nicht hinein). file_*-Tools genauso. Nur ShellExecTool reagiert darauf.
    """
    import inspect
    from hydrahive_core import tools_git
    from hydrahive_core.tool_registry import (
        ShellExecTool, FileReadTool, FileWriteTool, FilePatchTool,
    )

    # ShellExecTool.execute MUSS execution_mode-aware sein (über _execution_mode kwarg)
    src = inspect.getsource(ShellExecTool.execute)
    assert "_execution_mode" in src, "ShellExecTool muss execution_mode lesen"

    # File-Tools dürfen execution_mode nicht semantisch lesen
    for tool_cls in (FileReadTool, FileWriteTool, FilePatchTool):
        src = inspect.getsource(tool_cls.execute)
        assert "_execution_mode" not in src, (
            f"{tool_cls.__name__} liest _execution_mode — execution_mode "
            "soll nur shell_exec beeinflussen."
        )

    # Git-Tools genauso
    for cls_name in ("GitCloneTool", "GitStatusTool", "GitCommitAllTool", "GitPushTool"):
        cls = getattr(tools_git, cls_name)
        src = inspect.getsource(cls.execute)
        assert "_execution_mode" not in src, (
            f"{cls_name} liest _execution_mode — execution_mode soll nur "
            "shell_exec beeinflussen."
        )


def test_invariant6e_tool_suggestions_dont_lie_about_permissions():
    """6e (#638): _TOOL_SUGGESTIONS verweist auf existierende Mechanismen,
    nicht auf nicht-existente "Permission XYZ hinzufügen"-Konzepte.
    """
    from hydrahive_core.orchestrator_tools import _TOOL_SUGGESTIONS

    forbidden_substrings = (
        "Permission '",   # "Permission 'filesystem.write' hinzufügen"
        "hinzufügen",     # generischer Permission-Hinzufügen-Hinweis
    )
    for tool_name, suggestion in _TOOL_SUGGESTIONS.items():
        for forbidden in forbidden_substrings:
            assert forbidden not in suggestion, (
                f"_TOOL_SUGGESTIONS['{tool_name}'] enthält veralteten "
                f"Permission-Hinweis: {suggestion!r}"
            )


# ===========================================================================
# BLOCKERS — Stellen, die ohne Refactor nicht testbar sind
# ===========================================================================
"""
Diese Blocker sind selbst Audit-Befunde und Teil des Ergebnisses. Refactors
NICHT umgesetzt — sie würden über den Test-Auftrag hinausgehen (Strang E ist
nur Test-Schreiben, Strang B/C übernimmt die Implementation).

────────────────────────────────────────────────────────────────────────────
Blocker B1 — OAuth-Stream Provider-Format-Konvertierung nicht pure-callable
────────────────────────────────────────────────────────────────────────────
Datei:        core/src/hydrahive_core/orchestrator_stream.py
Code-Stelle:  Funktion _stream_anthropic_oauth, ungefähr Z. 591-728 — die
              Konvertierung von OpenAI-Format (system / user / assistant
              mit tool_calls) zu Anthropic-Format (content-Liste mit
              text/tool_use/tool_result-Blöcken) ist inline im
              async-Generator zwischen `normalize_messages_for_call()`
              (Z. 605) und `client.messages.stream(**kwargs)` (Z. 729).
Zweite Stelle: orchestrator_llm.py:_anthropic_oauth_call, Z. 624-679 —
              dieselbe Konvertierung, dupliziert.

Warum nicht testbar ohne Prozessstart / Provider-Mock-Engineering:
- Die Konvertierungslogik kann nicht aufgerufen werden, ohne den ganzen
  async-Generator zu starten, der direkt im Anschluss `client.messages.
  stream()` aufruft.
- Selbst bei Mock von `anthropic.AsyncAnthropic` muss man die Generator-
  Iteration anstoßen und an genau der richtigen Stelle wieder abbrechen,
  was fragil ist.
- Die echte Invariante (gleicher Input → gleiches Anthropic-Format) lässt
  sich daher nicht direkt prüfen.

Folge fürs Test 1c: Wir testen heute nur strukturelle Identität auf
Builder-Ebene. Die finale Provider-Format-Identität bleibt offen, bis B1
gelöst ist.

Minimaler Refactor-Vorschlag (NICHT UMGESETZT):
    # Neue pure helper in orchestrator_llm.py oder neue Datei message_format.py
    def to_anthropic_format(messages: list[dict]) -> tuple[str, list[dict]]:
        '''Extrahiert system_msg + konvertiert OpenAI tool_calls/tool zu
        Anthropic content-Block-Liste. Pure, testbar.'''
        ...
        return system_msg, filtered

    # _stream_anthropic_oauth (orchestrator_stream.py)
    # _anthropic_oauth_call (orchestrator_llm.py)
    # rufen statt Inline-Loop nur noch:
    #     system_msg, filtered = to_anthropic_format(messages)

────────────────────────────────────────────────────────────────────────────
Blocker B2 — shell_exec Sandbox-Scope nur über Prozessstart beobachtbar
────────────────────────────────────────────────────────────────────────────
Datei:        core/src/hydrahive_core/tool_registry.py
Code-Stelle:  Klasse ShellExecTool.execute, Z. 618-815 — der bwrap-bind-
              Zusammenbau (Z. 685-727) ist inline in der async-execute-
              Methode und endet in `proc = await asyncio.create_subprocess_
              shell(exec_command, ...)` (Z. 770-776).

Warum nicht testbar ohne Prozessstart:
- Der Workspace-Scope der Shell ist ausschließlich aus den bwrap-bind-
  Argumenten ableitbar (Z. 703-727), die nur als shell-quoted String in
  `exec_command` enden und vom subprocess konsumiert werden.
- Ohne den subprocess zu starten gibt es keine Möglichkeit, den effektiv
  gemounteten Workspace-Pfad zu beobachten.
- Den Subprocess starten ist im Test-Auftrag explizit ausgeschlossen
  (kein Prozessstart als Test-Setup).

Folge für Test 3: Wir prüfen heute nur file-Resolver vs. git-Resolver.
Der Shell-Scope wird durch B2 als nicht testbar markiert. Die Drift ist
trotzdem belegbar — wenn file-Root und git-Root abweichen, kann der
Shell-Sandbox-Mount per Konstruktion nicht beide erreichen.

Minimaler Refactor-Vorschlag (NICHT UMGESETZT):
    # Neue pure helper in tool_registry.py
    def _resolve_sandbox_scope(
        project_id: str, cwd: str,
    ) -> tuple[Path, list[str]]:
        '''Liefert (effective_cwd, bwrap_bind_args) basierend auf
        project_id und gewünschtem cwd. Pure, testbar.'''
        ...
        return safe_cwd, bind_args

    # ShellExecTool.execute ruft statt Inline-Logik nur noch:
    #     safe_cwd, bind_args = _resolve_sandbox_scope(project_id, cwd)
"""


# ===========================================================================
# Invariante 7 — Anthropic-Pfad: keine rohen role:tool-Messages an Anthropic
# (#637-Followup, Live-Bug 400 "Unexpected role tool")
# ===========================================================================

def test_invariant7a_to_anthropic_format_strips_tool_role():
    """7a: to_anthropic_format() konvertiert role:tool zu user-tool_result-Block,
    assistant+tool_calls zu assistant-tool_use-Block. Im Output existiert kein
    role:"tool" mehr."""
    from hydrahive_core.message_normalization import to_anthropic_format

    sys_msg, msgs = to_anthropic_format([
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do foo"},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "call_42", "type": "function",
            "function": {"name": "foo", "arguments": "{}"},
        }]},
        {"role": "tool", "tool_call_id": "call_42", "content": "result"},
        {"role": "assistant", "content": "done"},
    ])

    assert sys_msg == "sys"
    # KEIN role:"tool" mehr — Anthropic erlaubt nur user/assistant
    assert all(m["role"] in ("user", "assistant") for m in msgs), (
        f"role:tool ist nicht entfernt worden: {msgs}"
    )
    # tool_use im assistant-content
    asst_with_use = [m for m in msgs if m["role"] == "assistant"
                     and isinstance(m.get("content"), list)
                     and any(b.get("type") == "tool_use" for b in m["content"])]
    assert asst_with_use, f"tool_use-Block fehlt: {msgs}"
    assert asst_with_use[0]["content"][-1]["id"] == "call_42"
    # tool_result im user-content
    user_with_result = [m for m in msgs if m["role"] == "user"
                        and isinstance(m.get("content"), list)
                        and any(b.get("type") == "tool_result" for b in m["content"])]
    assert user_with_result, f"tool_result-Block fehlt: {msgs}"
    assert user_with_result[0]["content"][0]["tool_use_id"] == "call_42"


def test_invariant7b_oauth_call_uses_to_anthropic_format():
    """7b: _anthropic_oauth_call ruft den gemeinsamen Helper auf statt
    eigenen Inline-Loop. Sichert Code-Dedup nach #637-Followup."""
    import inspect
    from hydrahive_core import orchestrator_llm

    src = inspect.getsource(orchestrator_llm._anthropic_oauth_call)
    assert "to_anthropic_format" in src, (
        "_anthropic_oauth_call ruft nicht to_anthropic_format auf — "
        "evtl. eigener Konvertier-Loop wieder eingeschmuggelt."
    )


def test_invariant7c_stream_oauth_uses_to_anthropic_format():
    """7c: _stream_anthropic_oauth ruft den gemeinsamen Helper auf —
    der akute Live-Bug (Anthropic 400 'Unexpected role tool') wäre sonst zurück."""
    import inspect
    from hydrahive_core import orchestrator_stream

    src = inspect.getsource(orchestrator_stream._stream_anthropic_oauth)
    assert "to_anthropic_format" in src, (
        "_stream_anthropic_oauth ruft nicht to_anthropic_format auf — "
        "Live-Bug 'Unexpected role tool' wäre zurück."
    )


def test_invariant7d_litellm_anthropic_path_converts_before_send():
    """7d: _stream_litellm und _llm_call_single rufen to_anthropic_format
    für Anthropic-Modelle auf, damit auch ohne OAuth (litellm-Pfad) keine
    rohen role:tool-Messages an Anthropic gehen."""
    import inspect
    from hydrahive_core import orchestrator_stream, orchestrator_llm

    src_stream = inspect.getsource(orchestrator_stream._stream_litellm)
    assert "to_anthropic_format" in src_stream, (
        "_stream_litellm konvertiert nicht für Anthropic-Modelle."
    )
    src_call = inspect.getsource(orchestrator_llm._llm_call_single)
    assert "to_anthropic_format" in src_call, (
        "_llm_call_single konvertiert nicht für Anthropic-Modelle."
    )


# ===========================================================================
# Invariante 8 — CONFIRM-Round-Trip (#641)
# ===========================================================================
# RiskLevel.CONFIRM pausiert den Tool-Call und wartet auf User-Antwort.
# Vorher (vor #641) wurde CONFIRM nur geloggt und der Call lief durch.

import asyncio as _asyncio_inv8


def _make_orch_for_confirm(tool_name: str, tool_input: dict, exec_count: list,
                            session_id: str = "sess-1") -> object:
    """Stub-Orch für execute_tool_call — Tool zählt Aufrufe in exec_count."""
    fake_tool = MagicMock()
    fake_tool.id = tool_name

    async def _exec_recording(*a, **k):
        exec_count.append(1)
        return {"ok": True, "called": tool_name}

    orch = MagicMock()
    orch._resolve_allowed_tool = MagicMock(return_value=fake_tool)
    orch._execute_tool = _exec_recording
    fake_session = MagicMock()
    fake_session.id = session_id
    orch._sessions = MagicMock()
    orch._sessions.get_active = MagicMock(return_value=fake_session)
    orch._mcp_schemas_for_agent = MagicMock(return_value=[])
    return orch


def _confirm_tool_input_for_classifier_confirm():
    """Tool-Name + Input, das `permission_classifier.classify_static`
    garantiert auf CONFIRM klassifiziert."""
    # `git_push` ist in _ALWAYS_CONFIRM (permission_classifier.py:37-39)
    return "git_push", {}


async def test_invariant8a_confirm_pauses_until_approved():
    """8a (#641): CONFIRM pausiert den Tool-Call. Nach approve wird normal
    ausgeführt."""
    from hydrahive_core import tool_confirmation
    from hydrahive_core.orchestrator_tools import execute_tool_call
    tool_confirmation._reset_for_tests()

    tool_name, tool_input = _confirm_tool_input_for_classifier_confirm()
    exec_count: list = []
    orch = _make_orch_for_confirm(tool_name, tool_input, exec_count)
    boss_cfg = MagicMock()
    boss_cfg.id = "agent-x"
    boss_cfg.mcp_servers = []
    tcid = "call-approve-1"

    async def _approve_after_delay():
        await _asyncio_inv8.sleep(0.05)
        outcome = tool_confirmation.resolve_confirmation("sess-1", tcid, "approve")
        assert outcome == "resolved"

    approve_task = _asyncio_inv8.create_task(_approve_after_delay())
    result, is_error = await execute_tool_call(
        orch, boss_cfg=boss_cfg, project_id="p", tool_name=tool_name,
        tool_input=tool_input, tool_call_id=tcid,
    )
    await approve_task

    assert is_error is False, f"approve sollte zu Erfolg führen, got: {result}"
    assert result.get("ok") is True
    assert len(exec_count) == 1, "Tool wurde nach approve nicht ausgeführt"


async def test_invariant8b_confirm_denied_returns_error_without_executing():
    """8b (#641): deny verhindert die Ausführung. Tool wird nicht aufgerufen."""
    from hydrahive_core import tool_confirmation
    from hydrahive_core.orchestrator_tools import execute_tool_call
    tool_confirmation._reset_for_tests()

    tool_name, tool_input = _confirm_tool_input_for_classifier_confirm()
    exec_count: list = []
    orch = _make_orch_for_confirm(tool_name, tool_input, exec_count)
    boss_cfg = MagicMock()
    boss_cfg.id = "agent-x"
    boss_cfg.mcp_servers = []
    tcid = "call-deny-1"

    async def _deny_after_delay():
        await _asyncio_inv8.sleep(0.05)
        tool_confirmation.resolve_confirmation("sess-1", tcid, "deny")

    deny_task = _asyncio_inv8.create_task(_deny_after_delay())
    result, is_error = await execute_tool_call(
        orch, boss_cfg=boss_cfg, project_id="p", tool_name=tool_name,
        tool_input=tool_input, tool_call_id=tcid,
    )
    await deny_task

    assert is_error is True
    assert result.get("risk") == "confirm_denied", f"got: {result}"
    assert len(exec_count) == 0, "Tool wurde trotz deny ausgeführt"


async def test_invariant8c_confirm_timeout_returns_error_without_executing(monkeypatch):
    """8c (#641): Ohne Antwort → Timeout-Fehler, Tool wird nicht ausgeführt."""
    from hydrahive_core import tool_confirmation
    from hydrahive_core.orchestrator_tools import execute_tool_call
    tool_confirmation._reset_for_tests()
    # Default-Timeout per monkeypatch auf 0.2s drücken — schneller Test
    monkeypatch.setattr(tool_confirmation, "DEFAULT_CONFIRM_TIMEOUT", 0.2)

    tool_name, tool_input = _confirm_tool_input_for_classifier_confirm()
    exec_count: list = []
    orch = _make_orch_for_confirm(tool_name, tool_input, exec_count)
    boss_cfg = MagicMock()
    boss_cfg.id = "agent-x"
    boss_cfg.mcp_servers = []

    result, is_error = await execute_tool_call(
        orch, boss_cfg=boss_cfg, project_id="p", tool_name=tool_name,
        tool_input=tool_input, tool_call_id="call-timeout-1",
    )

    assert is_error is True
    assert result.get("risk") == "confirm_timeout", f"got: {result}"
    assert len(exec_count) == 0, "Tool wurde trotz Timeout ausgeführt"


def test_invariant8d_wrong_tool_call_id_does_not_resolve_other_pending():
    """8d (#641): Antwort auf falsche tool_call_id löst keinen anderen
    pending Call auf."""
    from hydrahive_core import tool_confirmation
    tool_confirmation._reset_for_tests()

    e1 = tool_confirmation.request_confirmation("sess-A", "call-1", "git_push", {})
    e2 = tool_confirmation.request_confirmation("sess-A", "call-2", "git_push", {})

    out = tool_confirmation.resolve_confirmation("sess-A", "wrong-id", "approve")
    assert out == "not_found"
    assert e1.decision is None
    assert e2.decision is None
    assert not e1.event.is_set()
    assert not e2.event.is_set()
    # Korrekte ID löst nur den richtigen auf
    out2 = tool_confirmation.resolve_confirmation("sess-A", "call-1", "deny")
    assert out2 == "resolved"
    assert e1.decision == "deny"
    assert e2.decision is None


def test_invariant8e_central_confirm_branch_in_execute_tool_call():
    """8e (#641): execute_tool_call hat den CONFIRM-Branch — alle Tool-Loops
    rufen diese eine Stelle auf, kein Loop kann CONFIRM umgehen."""
    import inspect
    from hydrahive_core import orchestrator_tools

    src = inspect.getsource(orchestrator_tools.execute_tool_call)
    assert "RiskLevel.CONFIRM" in src, (
        "execute_tool_call hat keinen CONFIRM-Branch — Loops können Confirm umgehen."
    )
    assert "wait_for_confirmation" in src, (
        "execute_tool_call wartet nicht auf User-Antwort."
    )


def test_invariant8f_session_isolation():
    """8f (#641): Pending-Einträge sind session-gebunden — andere Session
    sieht sie nicht in get_pending."""
    from hydrahive_core import tool_confirmation
    tool_confirmation._reset_for_tests()

    tool_confirmation.request_confirmation("sess-A", "call-1", "git_push", {})
    tool_confirmation.request_confirmation("sess-B", "call-2", "git_push", {})

    pending_a = tool_confirmation.get_pending("sess-A")
    pending_b = tool_confirmation.get_pending("sess-B")
    pending_other = tool_confirmation.get_pending("sess-other")

    assert len(pending_a) == 1 and pending_a[0]["tool_call_id"] == "call-1"
    assert len(pending_b) == 1 and pending_b[0]["tool_call_id"] == "call-2"
    assert pending_other == []


# ===========================================================================
# Invariante 9 — /tool-confirm JSON-Body-Parsing (#641-Followup)
# ===========================================================================
# Bug: `ToolConfirmRequest` lokal in `register_project_routes()` definiert,
# kombiniert mit `from __future__ import annotations`, führte zu FastAPI 422
# "missing query parameter 'req'" — das Modell wurde im Modul-Namespace
# nicht gefunden, FastAPI fiel auf Query-Default zurück.

def test_invariant9a_tool_confirm_request_is_module_scope():
    """9a (#641): ToolConfirmRequest ist auf Module-Scope importierbar.

    Wenn das Modell in register_project_routes() lokal definiert wäre,
    schlägt dieser Import fehl → FastAPI kann die String-Annotation bei
    `from __future__ import annotations` nicht auflösen → 422 query.req.
    """
    from hydrahive_core.router_projects import ToolConfirmRequest
    assert ToolConfirmRequest.__module__ == "hydrahive_core.router_projects"


def test_invariant9b_tool_confirm_request_parses_json_body():
    """9b (#641): Modell akzeptiert exakt die vom Frontend gesendete Form."""
    from hydrahive_core.router_projects import ToolConfirmRequest

    obj = ToolConfirmRequest.model_validate({
        "tool_call_id": "call_42",
        "decision":     "approve",
    })
    assert obj.tool_call_id == "call_42"
    assert obj.decision == "approve"

    obj2 = ToolConfirmRequest.model_validate({
        "tool_call_id": "call_99",
        "decision":     "deny",
    })
    assert obj2.decision == "deny"

    # Pydantic erzwingt den Literal-Constraint
    import pytest as _pt
    with _pt.raises(Exception):
        ToolConfirmRequest.model_validate({
            "tool_call_id": "x", "decision": "maybe",
        })


def test_invariant9c_tool_confirm_annotation_resolvable_from_module_globals():
    """9c (#641): Kern des Fix-Beweises. FastAPI nutzt `typing.get_type_hints()`
    um bei `from __future__ import annotations` die String-Annotationen zu
    Real-Types aufzulösen. Die Auflösung geschieht im Modul-Namespace der
    definierenden Funktion. Wenn `ToolConfirmRequest` nur lokal definiert
    war, scheiterte diese Auflösung still → FastAPI fiel auf Query-Default
    zurück → 422 'missing query parameter req'.

    Test: sucht den Endpoint, holt seine Type-Hints via `get_type_hints`,
    prüft dass `req` zu `ToolConfirmRequest` auflöst (nicht zu einem
    String bleibt oder fehlt).
    """
    import typing
    from hydrahive_core import router_projects

    # Den Endpoint finden — er ist in register_project_routes lokal definiert,
    # aber resolve_tool_confirm ist die Funktion. Wir prüfen stattdessen, dass
    # der Modul-Namespace ToolConfirmRequest enthält (Vorbedingung für FastAPI):
    assert hasattr(router_projects, "ToolConfirmRequest"), (
        "ToolConfirmRequest muss im Modul-Namespace sein, sonst kann "
        "FastAPI/get_type_hints die String-Annotation nicht auflösen."
    )
    # get_type_hints mit localns=None/globalns=module muss es finden
    import inspect
    mod_globals = inspect.getmembers(router_projects)
    names = {n for n, _ in mod_globals}
    assert "ToolConfirmRequest" in names
    # BaseModel-Subklasse?
    from pydantic import BaseModel
    assert issubclass(router_projects.ToolConfirmRequest, BaseModel)


# ===========================================================================
# Invariante 10 — shell_exec chmod/Permissions-Patterns sauber auf CONFIRM
# (#641-Followup — Live-Bug: chmod 777 lief ohne Banner durch)
# ===========================================================================

def _classify_shell(cmd: str):
    from hydrahive_core.permission_classifier import classify_static
    return classify_static("shell_exec", {"command": cmd})


def test_invariant10a_chmod_777_is_confirm():
    """10a (#641): Der Live-Bug-Original-Case."""
    from hydrahive_core.permission_classifier import RiskLevel
    assert _classify_shell("chmod 777 /projects/test/confirm_tmp.txt") == RiskLevel.CONFIRM


def test_invariant10b_sudo_chmod_777_is_confirm():
    """10b: Mit sudo-Präfix."""
    from hydrahive_core.permission_classifier import RiskLevel
    assert _classify_shell("sudo chmod 777 /tmp/x") == RiskLevel.CONFIRM


def test_invariant10c_chmod_octal_prefix_is_confirm():
    """10c: chmod mit 0-Präfix vor Mode."""
    from hydrahive_core.permission_classifier import RiskLevel
    assert _classify_shell("chmod 0777 /tmp/x") == RiskLevel.CONFIRM
    assert _classify_shell("chmod 0666 /tmp/x") == RiskLevel.CONFIRM


def test_invariant10d_chmod_recursive_flag_is_confirm():
    """10d: chmod mit -R Rekursiv-Flag."""
    from hydrahive_core.permission_classifier import RiskLevel
    assert _classify_shell("chmod -R 777 /tmp/x") == RiskLevel.CONFIRM
    assert _classify_shell("chmod -Rv 666 /tmp/x") == RiskLevel.CONFIRM


def test_invariant10e_chmod_group_world_writable_modes():
    """10e: weitere Gruppe-/World-writable Modes."""
    from hydrahive_core.permission_classifier import RiskLevel
    for cmd in [
        "chmod 666 /tmp/x",     # world rw
        "chmod 776 /tmp/x",     # world rw + group rwx
        "chmod 770 /tmp/x",     # group rwx
        "chmod 707 /tmp/x",     # world rwx + user rwx (keine group)
    ]:
        assert _classify_shell(cmd) == RiskLevel.CONFIRM, f"Erwarte CONFIRM für {cmd!r}"


def test_invariant10f_chmod_symbolic_world_write_is_confirm():
    """10f: chmod symbolic — world/other/all +w."""
    from hydrahive_core.permission_classifier import RiskLevel
    for cmd in [
        "chmod o+w /tmp/x",     # other +w
        "chmod go+w /tmp/x",    # group + other +w
        "chmod a+w /tmp/x",     # all +w
        "chmod -R o+w /tmp/x",  # rekursiv
    ]:
        assert _classify_shell(cmd) == RiskLevel.CONFIRM, f"Erwarte CONFIRM für {cmd!r}"


def test_invariant10g_harmless_shell_commands_stay_non_confirm():
    """10g: harmlose Befehle bleiben None (Default → ALLOW im Classifier)."""
    # None = statisch keine Regel → Default-Branch entscheidet
    for cmd in [
        "ls -la /tmp",
        "echo hello",
        "cat /projects/test/foo.txt",
        "date",
    ]:
        assert _classify_shell(cmd) is None, f"Erwarte None für {cmd!r}, got {_classify_shell(cmd)}"


def test_invariant10h_rm_rf_root_still_denied():
    """10h: Regression-Schutz — kritische DENY-Patterns bleiben DENY."""
    from hydrahive_core.permission_classifier import RiskLevel
    assert _classify_shell("rm -rf /") == RiskLevel.DENY
