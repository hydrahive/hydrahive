"""
working_state.py — Working-Memory-Schicht + Resume-Snapshots (#630, #632)

Klare Trennung der Memory-Schichten:

- **Projekt-Memory** — persistent, agentenweit (`agent_dir/memory/*.md`).
- **Session-Memory** — persistent, sessionweit (Messages, Snapshots in SQLite).
- **Working-Memory** — letzter Snapshot der laufenden Session: offene Files,
  letzte Tool-Calls, Memory-Treffer, Compaction-Stand, Budget-Entscheidungen,
  Git-Workspace-State. Wird beim Resume reaktiviert und in den
  `working_state`-Channel (#627) injiziert.
- **Turn-Kontext** — flüchtig, nur in der aktuellen Runde.

Snapshots werden pro Turn-Ende geschrieben, beim Resume wird der jüngste
Snapshot geladen.

Format ist versioniert (SCHEMA_VERSION) damit zukünftige Felder rückwärts-
kompatibel ergänzt werden können.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


SCHEMA_VERSION = 2  # +last_tools, +git_state (#632)


@dataclass
class WorkingState:
    """Snapshot des Sessions-Arbeitsgedächtnisses am Ende eines Turns."""

    schema_version: int = SCHEMA_VERSION

    # Aktuelles Ziel / Aufgabe (1-Liner aus letzter User-Message)
    current_goal: str = ""

    # Wann wurde dieser Snapshot erzeugt (UTC ISO)
    created_at: str = ""

    # ── Was im letzten Turn passierte (#632) ─────────────────────────────

    # Letzte Tool-Calls — kompakt, max 10. Format:
    #   {"name": "shell_exec", "summary": "git rebase --abort", "ok": true}
    last_tools: list[dict] = field(default_factory=list)

    # Files die im letzten Turn gelesen / geschrieben wurden (sortiert nach
    # Recency, neueste zuerst)
    open_files: list[str] = field(default_factory=list)

    # Git-Workspace-State pro relevantem Projektverzeichnis (#632 + #633).
    # Liste von Dicts mit: path, branch, ahead, behind, uncommitted (Liste),
    # rebase_in_progress, merge_in_progress.
    git_state: list[dict] = field(default_factory=list)

    # ── Bestehende Felder ───────────────────────────────────────────────

    # Letzte aktive Memory-Treffer (BM25/A-MEM): Source-Pfade
    last_memory_hits: list[str] = field(default_factory=list)

    # Aktive (noch nicht abgeschlossene) Tool-Aufrufe — z.B. Background-Tasks
    active_tools: list[dict] = field(default_factory=list)

    # Wie viel Compaction wurde im letzten Turn angewandt
    compaction_state: dict = field(default_factory=dict)

    # Budget-Entscheidungen: was wurde gekürzt, warum
    budget_decisions: list[str] = field(default_factory=list)

    # ── Serialisierung ──────────────────────────────────────────────────

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "WorkingState":
        try:
            data = json.loads(raw or "{}")
        except Exception:
            return cls()
        # Forward-compatible: unbekannte Felder ignorieren, Defaults für fehlende
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in data.items() if k in known}
        return cls(**clean)

    # ── Channel-Render ──────────────────────────────────────────────────

    def to_channel_text(self) -> str:
        """Format für Injektion in den `working_state`-Channel (#627).

        Bewusst kompakt — der Snapshot soll Hintergrund liefern, nicht die
        Hauptaufmerksamkeit beanspruchen. Reihenfolge nach Wichtigkeit:
        Anomalien zuerst (rebase/merge in progress), dann Goal/Files/Tools.
        """
        lines: list[str] = []

        # 1. Workspace-Anomalien GANZ OBEN (User-Aufmerksamkeit)
        anomalies = [g for g in self.git_state if g.get("rebase_in_progress")
                     or g.get("merge_in_progress") or g.get("unmerged")]
        for g in anomalies:
            path = g.get("path", "?")
            if g.get("rebase_in_progress"):
                lines.append(
                    f"⚠ Workspace `{path}` steckt in Rebase. "
                    "Erst lösen (`git rebase --continue` / `--abort`) bevor "
                    "neue Operationen."
                )
            elif g.get("merge_in_progress"):
                lines.append(
                    f"⚠ Workspace `{path}` hat unfertigen Merge. "
                    "Erst lösen (`git merge --continue` / `--abort`)."
                )
            unmerged = g.get("unmerged") or []
            if unmerged:
                lines.append(f"   Konflikte: {', '.join(unmerged[:5])}")

        # 2. Aktuelles Ziel
        if self.current_goal:
            lines.append(f"**Aktuelles Ziel:** {self.current_goal}")

        # 3. Letzte Tool-Calls — wichtig damit Agent versteht was lief
        if self.last_tools:
            tool_lines = []
            for t in self.last_tools[-5:]:
                marker = "✓" if t.get("ok") else "✗"
                summary = t.get("summary", "")[:80]
                tool_lines.append(f"  {marker} {t.get('name','?')}: {summary}")
            lines.append("**Letzte Tool-Calls:**\n" + "\n".join(tool_lines))

        # 4. Offene Files
        if self.open_files:
            lines.append(f"**Zuletzt berührte Files:** {', '.join(self.open_files[:8])}")

        # 5. Git-State (kompakt, ohne Anomalien — die kamen schon oben)
        normal_states = [g for g in self.git_state if not (g.get("rebase_in_progress")
                          or g.get("merge_in_progress") or g.get("unmerged"))]
        for g in normal_states:
            ahead = g.get("ahead", 0); behind = g.get("behind", 0)
            uncommitted = g.get("uncommitted", [])
            parts = []
            if ahead or behind:
                parts.append(f"↑{ahead} ↓{behind}")
            if uncommitted:
                parts.append(f"{len(uncommitted)} uncommitted")
            if parts:
                lines.append(f"**Git** `{g.get('path','?')}` ({g.get('branch','?')}): {' · '.join(parts)}")

        # 6. Memory + Compaction (am Ende)
        if self.last_memory_hits:
            lines.append(f"**Zuletzt aktive Memories:** {', '.join(self.last_memory_hits[:8])}")
        if self.budget_decisions:
            lines.append(f"**Budget-Entscheidungen:** {'; '.join(self.budget_decisions[:3])}")
        if self.compaction_state:
            lines.append(f"**Compaction:** {json.dumps(self.compaction_state, ensure_ascii=False)}")

        if not lines:
            return ""
        header = "## Working-Memory (Snapshot vom letzten Turn)"
        if self.created_at:
            header += f" — {self.created_at}"
        return header + "\n\n" + "\n".join(lines)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Helpers für Snapshot-Build (#632 H2/H3) ───────────────────────────────

_TOOL_SUMMARY_MAX = 80
_LAST_TOOLS_KEEP = 10


def summarize_tool_message(role_name: str, content: str, tool_name: str = "",
                            ok: bool | None = None) -> dict:
    """Formt eine Message in einen kompakten last_tools-Eintrag."""
    summary = (content or "").strip().splitlines()[0] if content else ""
    summary = summary[:_TOOL_SUMMARY_MAX]
    out = {"name": tool_name or role_name, "summary": summary}
    if ok is not None:
        out["ok"] = bool(ok)
    return out


def extract_open_files_from_messages(messages, *, max_files: int = 8) -> list[str]:
    """Liest die letzten Tool-Calls/Results und extrahiert berührte File-Pfade."""
    seen: list[str] = []
    for m in reversed(messages):
        meta = getattr(m, "metadata", None) or {}
        for key in ("path", "file_path", "filename"):
            p = meta.get(key)
            if p and p not in seen:
                seen.append(p)
                if len(seen) >= max_files:
                    return seen
    return seen


def compute_git_state(project_dir: str | Path) -> dict | None:
    """Liefert kompaktes Workspace-Anomalie-Bild für ein Git-Repo.

    Gibt None zurück wenn das Verzeichnis kein Git-Repo ist oder git
    nicht verfügbar ist. Wirft nie. Read-only — kein Repo-Mutation.
    """
    p = Path(project_dir)
    if not p.exists() or not (p / ".git").exists():
        return None

    def _run(args: list[str]) -> str:
        try:
            r = subprocess.run(
                ["git", "-C", str(p)] + args,
                capture_output=True, text=True, timeout=4,
            )
            return r.stdout
        except Exception:
            return ""

    state: dict = {"path": str(p)}

    branch = _run(["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    if branch:
        state["branch"] = branch

    # Anomalien: rebase / merge in progress
    state["rebase_in_progress"] = (
        (p / ".git" / "rebase-merge").exists() or
        (p / ".git" / "rebase-apply").exists()
    )
    state["merge_in_progress"] = (p / ".git" / "MERGE_HEAD").exists()

    # ahead/behind vs upstream (best effort)
    ab = _run(["rev-list", "--left-right", "--count", "@{u}...HEAD"]).strip().split()
    if len(ab) == 2:
        try:
            state["behind"] = int(ab[0]); state["ahead"] = int(ab[1])
        except ValueError:
            pass

    # uncommitted + unmerged Pfade
    porcelain = _run(["status", "--porcelain"]).splitlines()
    uncommitted: list[str] = []
    unmerged: list[str] = []
    for line in porcelain[:50]:
        if not line or len(line) < 4:
            continue
        code = line[:2]
        path = line[3:]
        if code in ("UU", "AA", "DD") or "U" in code:
            unmerged.append(path)
        elif code != "  ":
            uncommitted.append(path)
    if uncommitted:
        state["uncommitted"] = uncommitted[:10]
    if unmerged:
        state["unmerged"] = unmerged[:10]

    return state
