"""compaction_summary.py — Strukturierter Task-State für Compaction (#660).

Liefert Bausteine für beide Compaction-Pfade (`_compact_if_needed` und den
`/agents/{id}/session/compact`-Endpoint):

- `TaskStateFacts` — deterministisch gesammelte Fakten aus `working_state`
  und Message-History (keine LLM-Heuristik).
- `collect_task_state_facts(session, boss_cfg)` — Sammler.
- `render_task_state_block(facts)` — Markdown-Block, der dem LLM als
  **Eingabe** vorgelegt wird („übernimm wörtlich").
- `build_summary_prompt(history_lines, facts, existing_summary=None)` —
  voller System+User-Prompt-Paar (zwei dicts) inkl. Output-Schema.
- `parse_summary_frontmatter(summary)` — extrahiert YAML-Frontmatter falls
  vorhanden; liefert None bei fehlendem/defektem Frontmatter. Kein Crash.
- `redact_summary_text(s)` — wendet die `#657`-Secret-Patterns ohne
  Length-Truncate an; für History-Lines und Final-Summary.

Scope V1 (siehe #660-Plan):
- Runtime-Felder `active_hooks`/`active_skills` sind best-effort; wenn
  keine saubere Quelle → `[]`. Wir bauen keine neuen Caches oder Journal-
  Scrapes.
- `tests` wird per Regex aus Tool-Result-Texten heuristisch gezogen.
- Kein `deploy`-Feld (projektspezifisch).
- `session_manager.compact` bleibt unangetastet; Maschinenlesbarkeit
  kommt ausschließlich über YAML-Frontmatter im Summary-Text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from .hook_runtime import _REDACT_PATTERNS as _SECRET_PATTERNS


# ── Redaction ────────────────────────────────────────────────────────────────

def redact_summary_text(s: str) -> str:
    """Secret-Redaction wie `hook_runtime._redact_str`, aber ohne
    Length-Truncate (Summary-Texte dürfen bis zu ~5–6 k Zeichen lang sein).
    Patterns werden aus `_REDACT_PATTERNS` wiederverwendet — EIN Source
    of Truth für alle Secret-Regexe im Core."""
    if not isinstance(s, str):
        return s
    for pat, repl in _SECRET_PATTERNS:
        s = pat.sub(repl, s)
    return s


# ── Fakten-Sammler ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TaskStateFacts:
    """Deterministisch gesammelter Runtime-Kontext für die Compaction.

    Alle Felder haben sichere Defaults. Wenn eine Quelle nicht verfügbar
    ist, bleibt das Feld leer — der Prompt instruiert das LLM trotzdem,
    das Feld nicht zu erfinden.
    """
    goal:           str               = ""
    files_touched:  list[str]         = field(default_factory=list)
    commits:        list[str]         = field(default_factory=list)
    issues:         list[str]         = field(default_factory=list)
    git_state:      list[dict]        = field(default_factory=list)
    last_tools:     list[dict]        = field(default_factory=list)
    tests:          dict              = field(default_factory=dict)  # {passed, failed, suites}
    runtime:        dict              = field(default_factory=dict)  # active_hooks/worktrees/skills/isolation_mode/write_scope
    next_step_hint: str               = ""


# Regex-Helfer
_COMMIT_HASH_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
_ISSUE_RE       = re.compile(r"#(\d{1,6})\b")
# Heuristik für pytest/vitest-Output. Beispiele:
#   "======================== 163 passed, 3 warnings in 2.87s ========================"
#   "1 failed, 12 passed"
_TESTS_PASSED_RE = re.compile(r"(\d+)\s+passed", re.IGNORECASE)
_TESTS_FAILED_RE = re.compile(r"(\d+)\s+failed", re.IGNORECASE)
_TESTS_SUITE_RE  = re.compile(r"tests?/([A-Za-z0-9_./-]+\.py)", re.IGNORECASE)

# Heuristik fuer file-Pfade in Tool-Argumenten (file_read/write/patch)
_TOOL_ARG_PATH_RE = re.compile(r"(?:path|file)[\"':\s=]+([^\s\"',)]+)")


def _extract_commits(texts: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for t in texts:
        if not isinstance(t, str):
            continue
        for m in _COMMIT_HASH_RE.finditer(t):
            h = m.group(0).lower()
            # 40-stellige oder 7–12-stellige Hex-Strings — wir akzeptieren
            # typische git-short-hashes (≥7) und full (40). Alles dazwischen
            # ist selten in natürlichem Text.
            if len(h) == 40 or 7 <= len(h) <= 12:
                seen.setdefault(h, None)
            if len(seen) >= 10:
                break
    return list(seen.keys())


def _extract_issues(texts: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for t in texts:
        if not isinstance(t, str):
            continue
        for m in _ISSUE_RE.finditer(t):
            seen.setdefault(f"#{m.group(1)}", None)
            if len(seen) >= 20:
                break
    return list(seen.keys())


def _extract_tests(tool_result_texts: list[str]) -> dict:
    """Aggregiert passed/failed über alle Tool-Result-Texte; sammelt
    grob die Suite-Namen aus `tests/...py`-Matches."""
    passed_total = 0
    failed_total = 0
    suites: dict[str, None] = {}
    for t in tool_result_texts:
        if not isinstance(t, str):
            continue
        for m in _TESTS_PASSED_RE.finditer(t):
            passed_total += int(m.group(1))
        for m in _TESTS_FAILED_RE.finditer(t):
            failed_total += int(m.group(1))
        for m in _TESTS_SUITE_RE.finditer(t):
            suites.setdefault(m.group(1), None)
            if len(suites) >= 20:
                break
    out: dict = {}
    if passed_total:
        out["passed"] = passed_total
    if failed_total:
        out["failed"] = failed_total
    if suites:
        out["suites"] = list(suites.keys())
    return out


def _extract_files_from_tools(messages) -> list[str]:
    """Greift file-Pfade aus Tool-Call-Argumenten in Messages ab. Dedupliziert,
    max 30 Einträge."""
    from .session_manager import MessageRole  # lazy

    seen: dict[str, None] = {}
    for m in messages:
        # Tool-Calls sind typischerweise in assistant-Messages oder system-Markern
        txt = getattr(m, "content", None) or ""
        if not isinstance(txt, str):
            continue
        if getattr(m, "role", None) in (MessageRole.ASSISTANT, MessageRole.SYSTEM, MessageRole.TOOL):
            for match in _TOOL_ARG_PATH_RE.finditer(txt):
                p = match.group(1).strip().strip("`\"'")
                if len(p) <= 256 and ("/" in p or p.endswith((".py", ".ts", ".tsx", ".md", ".json", ".yaml", ".yml", ".sh", ".toml"))):
                    seen.setdefault(p, None)
                if len(seen) >= 30:
                    break
    return list(seen.keys())


def _last_user_message(messages) -> str:
    from .session_manager import MessageRole  # lazy
    for m in reversed(messages):
        if getattr(m, "role", None) == MessageRole.USER:
            content = getattr(m, "content", None) or ""
            if not isinstance(content, str):
                return ""
            return content[:200]
    return ""


def _collect_runtime_best_effort(session, boss_cfg) -> dict:
    """Best-effort Runtime-Kontext. V1-Regel: keine neuen Caches, kein
    Turn-Journal-Scraping erzwingen. Wenn etwas nicht greifbar ist → []."""
    active_skills: list[str] = []
    # active_tools aus working_state ist der einzige bereits persistente
    # Laufzeit-State, der für skill-Signale taugt. Skills-Tracking pro Turn
    # kommt erst mit #668-Follow-ups; deshalb: Platzhalter.
    try:
        ws = getattr(session, "working_state", None)
        if ws is not None and getattr(ws, "active_tools", None):
            # Nur Namen — keine Payload, keine Arguments.
            active_skills = [str(t.get("name", "")) for t in ws.active_tools
                             if isinstance(t, dict) and t.get("name")][:10]
    except Exception:
        active_skills = []

    worktrees: list[str] = []
    try:
        from .settings import settings as _s
        meta_dir = _s.worktrees_dir / "meta"
        session_id = getattr(session, "id", None)
        if meta_dir.exists() and session_id:
            import json
            for p in sorted(meta_dir.glob("*.json"))[-50:]:
                try:
                    meta = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if meta.get("parent_session_id") == session_id:
                    wt_id = meta.get("worktree_id") or p.stem
                    worktrees.append(wt_id)
                if len(worktrees) >= 10:
                    break
    except Exception:
        worktrees = []

    # Hooks + isolation_mode + write_scope: V1 keine saubere Quelle ohne
    # Neu-Scraping. Leere Defaults, Schema bleibt stabil.
    return {
        "active_hooks":   [],
        "worktrees":      worktrees,
        "active_skills":  active_skills,
        "isolation_mode": None,
        "write_scope":    [],
    }


def collect_task_state_facts(session, boss_cfg) -> TaskStateFacts:
    """Sammelt deterministische Fakten aus Session-State und Message-
    History. Wirft nicht — jede Quelle hat einen Fallback."""
    messages = getattr(session, "messages", []) or []

    goal = ""
    try:
        ws = getattr(session, "working_state", None)
        if ws is not None:
            _cg = getattr(ws, "current_goal", "")
            if isinstance(_cg, str) and _cg:
                goal = _cg
    except Exception:
        goal = ""

    git_state: list[dict] = []
    last_tools: list[dict] = []
    try:
        ws = getattr(session, "working_state", None)
        if ws is not None:
            _gs = getattr(ws, "git_state", None) or []
            _lt = getattr(ws, "last_tools", None) or []
            if isinstance(_gs, list):
                git_state = list(_gs)
            if isinstance(_lt, list):
                last_tools = list(_lt)[-10:]
    except Exception:
        pass

    # Texte für Regex-Extraktion. Robust gegen MagicMock-Messages in Tests:
    # non-string content wird übersprungen.
    from .session_manager import MessageRole  # lazy
    def _text_of(m) -> str:
        c = getattr(m, "content", None)
        return c if isinstance(c, str) else ""
    tool_result_texts = [_text_of(m) for m in messages
                         if getattr(m, "role", None) == MessageRole.TOOL]
    all_texts = [_text_of(m) for m in messages]

    files_touched: list[str] = []
    try:
        ws = getattr(session, "working_state", None)
        _of = getattr(ws, "open_files", None) if ws is not None else None
        if isinstance(_of, list):
            files_touched.extend(str(f) for f in _of[:20] if isinstance(f, str))
    except Exception:
        pass
    # Zusatz aus Tool-Args (deduped)
    for p in _extract_files_from_tools(messages):
        if p not in files_touched:
            files_touched.append(p)
        if len(files_touched) >= 30:
            break

    commits = _extract_commits(tool_result_texts)
    issues  = _extract_issues(all_texts)
    tests   = _extract_tests(tool_result_texts)
    runtime = _collect_runtime_best_effort(session, boss_cfg)

    return TaskStateFacts(
        goal=goal,
        files_touched=files_touched,
        commits=commits,
        issues=issues,
        git_state=git_state,
        last_tools=last_tools,
        tests=tests,
        runtime=runtime,
        next_step_hint=_last_user_message(messages),
    )


# ── Prompt-Rendering ─────────────────────────────────────────────────────────

def _fmt_list(items: list[str], *, max_items: int = 20) -> str:
    if not items:
        return "[]"
    trimmed = items[:max_items]
    more = f" …(+{len(items) - max_items})" if len(items) > max_items else ""
    return "[" + ", ".join(str(i) for i in trimmed) + "]" + more


def render_task_state_block(facts: TaskStateFacts) -> str:
    """Markdown-Block, der dem LLM als EINGABE vorgelegt wird.

    Regel an das Modell: die Fakten-Zeilen wörtlich übernehmen, nicht
    reinterpretieren. Freie Textteile (scope_in/out, risks, next_step,
    Body-Sektionen) formuliert das Modell selbst."""
    lines: list[str] = []
    lines.append("## Runtime-Kontext (Fakten — übernimm wörtlich, keine Reinterpretation)")
    lines.append("")
    lines.append(f"- **Ziel**: {redact_summary_text(facts.goal) or '(unbekannt)'}")
    lines.append(f"- **Offene Dateien**: {_fmt_list(facts.files_touched)}")
    if facts.last_tools:
        tool_repr = []
        for t in facts.last_tools[-5:]:
            name = str(t.get("name", "?"))
            ok   = "ok" if t.get("ok", True) else "fail"
            summ = redact_summary_text(str(t.get("summary", "")))[:120]
            tool_repr.append(f"{name}[{ok}] {summ}".rstrip())
        lines.append("- **Letzte Tool-Calls**: " + " | ".join(tool_repr))
    else:
        lines.append("- **Letzte Tool-Calls**: []")
    if facts.git_state:
        git_fragments = []
        for g in facts.git_state[:5]:
            branch = g.get("branch", "?")
            ahead  = g.get("ahead", 0)
            behind = g.get("behind", 0)
            dirty  = "dirty" if g.get("uncommitted") else "clean"
            git_fragments.append(f"{g.get('path','?')}@{branch} ({dirty}, +{ahead}/-{behind})")
        lines.append("- **Git**: " + "; ".join(git_fragments))
    else:
        lines.append("- **Git**: []")
    lines.append(f"- **Commits**: {_fmt_list(facts.commits, max_items=10)}")
    lines.append(f"- **Issues**: {_fmt_list(facts.issues, max_items=10)}")
    if facts.tests:
        t_passed = facts.tests.get("passed")
        t_failed = facts.tests.get("failed")
        t_suites = facts.tests.get("suites") or []
        line = "- **Tests**: "
        parts = []
        if t_passed is not None:
            parts.append(f"{t_passed} passed")
        if t_failed is not None:
            parts.append(f"{t_failed} failed")
        if t_suites:
            parts.append(f"suites={_fmt_list(t_suites, max_items=8)}")
        line += ", ".join(parts) if parts else "(keine Signale)"
        lines.append(line)
    else:
        lines.append("- **Tests**: (keine Signale)")
    r = facts.runtime or {}
    lines.append(f"- **Runtime.active_hooks**: {_fmt_list(r.get('active_hooks') or [], max_items=10)}")
    lines.append(f"- **Runtime.worktrees**: {_fmt_list(r.get('worktrees') or [], max_items=10)}")
    lines.append(f"- **Runtime.active_skills**: {_fmt_list(r.get('active_skills') or [], max_items=10)}")
    if r.get("isolation_mode"):
        lines.append(f"- **Runtime.isolation_mode**: {r['isolation_mode']}")
    if r.get("write_scope"):
        lines.append(f"- **Runtime.write_scope**: {_fmt_list(r['write_scope'])}")
    if facts.next_step_hint:
        lines.append(f"- **next_step_hint**: {redact_summary_text(facts.next_step_hint)}")
    return "\n".join(lines)


_OUTPUT_SCHEMA_INSTRUCTION = """\
## Ausgabe-Format

Beginne deine Antwort mit einem YAML-Frontmatter-Block (genau drei Bindestriche
oben und unten). Danach folgt die freie Markdown-Summary. Kein Text VOR dem
Frontmatter.

```
---
schema: compaction-task-state@1
goal: <kurzer Satz>
scope_in: [<item>, ...]
scope_out: [<item>, ...]
files_touched: [<path>, ...]
commits: [<hash>, ...]
issues: ["#660", ...]
tests:
  passed: <int|null>
  failed: <int|null>
  suites: [<path>, ...]
risks: [<text>, ...]
next_step: <text>
runtime:
  active_hooks: [<name>, ...]
  worktrees:   [<id>, ...]
  active_skills: [<name>, ...]
  isolation_mode: <safe|patch_only|write_scoped|null>
  write_scope: [<glob>, ...]
---

## Aktueller Arbeitskontext (WICHTIGSTER ABSCHNITT)
<Was GERADE läuft; Datei/Verzeichnis; letzte Aktion, erwarteter nächster Schritt.>

## Ziel
<Übergeordnetes Ziel.>

## Kontext & Entscheidungen
<Fakten, Constraints, Pfade, getroffene Entscheidungen.>

## Tool-Nutzung
<Welche Tools, welche Ergebnisse. Nur tatsächlich ausgeführte Calls.>

## Fortschritt
### Erledigt
- [x] ...
### In Arbeit
- [ ] ...
### Blockiert
- **Problem**: ...
```

KRITISCH:
- Die Fakten-Zeilen aus dem oben gelieferten `## Runtime-Kontext`-Block
  werden im Frontmatter wörtlich übernommen (goal, files_touched, commits,
  issues, tests, runtime.*). Nicht erfinden, nicht umformulieren.
- Wenn eine Fakten-Quelle leer ist → leere Liste bzw. null. Nicht raten.
- scope_in, scope_out, risks, next_step, Body-Sektionen formulierst du
  selbst aus der Konversation.
- Antworte NUR mit Frontmatter + Summary. Keine Einleitung."""


def build_summary_prompt(
    history_lines: str,
    facts: TaskStateFacts,
    *,
    existing_summary: str = "",
) -> list[dict]:
    """Baut das zweiteilige Prompt-Message-Array für den LLM-Compaction-Call.

    - `history_lines` wird durch `redact_summary_text` geschleust, bevor
      es in den User-Content wandert.
    - `facts` landen im System-Prompt als deterministischer Kontext-Block.
    - `existing_summary` ist optional: bei Stufe-1-Kette (bestehende
      Zusammenfassung + neue Messages) wird der System-Prompt angepasst.
    """
    state_block = render_task_state_block(facts)
    redacted_history = redact_summary_text(history_lines)

    if existing_summary:
        redacted_existing = redact_summary_text(existing_summary)
        system_instruction = (
            "Du erstellst eine strukturierte, maschinenlesbare Zusammenfassung "
            "der bisherigen Konversation. Du bekommst eine bisherige "
            "Zusammenfassung plus neue Nachrichten; aktualisiere sie — behalte "
            "alles Wichtige, verschiebe erledigte Punkte nach 'Erledigt'.\n\n"
            + state_block + "\n\n"
            + _OUTPUT_SCHEMA_INSTRUCTION
        )
        user_content = (
            f"BISHERIGE ZUSAMMENFASSUNG:\n{redacted_existing}\n\n"
            f"NEUE NACHRICHTEN:\n{redacted_history}"
        )
    else:
        system_instruction = (
            "Du erstellst eine strukturierte, maschinenlesbare Zusammenfassung "
            "der folgenden Konversation.\n\n"
            + state_block + "\n\n"
            + _OUTPUT_SCHEMA_INSTRUCTION
        )
        user_content = redacted_history

    return [
        {"role": "system", "content": system_instruction},
        {"role": "user",   "content": user_content},
    ]


# ── Frontmatter-Parser ───────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)


def parse_summary_frontmatter(summary: str) -> dict | None:
    """Extrahiert das YAML-Frontmatter aus einem Summary-Text.

    Rückgaben:
    - dict mit den Frontmatter-Feldern wenn sauber geparst,
    - None wenn kein Frontmatter vorhanden, leer oder defekt.

    Wirft nicht; kein Retry. Der freie Markdown-Body bleibt in jedem Fall
    nutzbar (Aufrufer speichert `summary` weiterhin 1:1)."""
    if not isinstance(summary, str) or not summary.strip():
        return None
    m = _FRONTMATTER_RE.match(summary.lstrip())
    if not m:
        return None
    body = m.group(1).strip()
    if not body:
        return None
    try:
        data = yaml.safe_load(body)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    return data


# ── Öffentliche Symbole ──────────────────────────────────────────────────────

__all__ = [
    "TaskStateFacts",
    "collect_task_state_facts",
    "render_task_state_block",
    "build_summary_prompt",
    "parse_summary_frontmatter",
    "redact_summary_text",
]
