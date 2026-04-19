"""
memory_diagnose.py — Diagnose + Cleanup fuer Legacy-Feedback-Dateien (#715, #716)

Seit #708 stehen Core-Regeln (Token-Disziplin, Memory-Konventionen, Bulk-Lookups
etc.) im System-Prompt via `prompts/agent_default_policy.md`. Frueher wurden die
gleichen Regeln pro Projekt in `memory/feedback_*.md`-Dateien geschrieben — diese
Duplikate sind jetzt redundant und toeten Cache-Effizienz.

Dieses Modul:
- Erkennt Legacy-Feedback-Dateien via expliziter Allowlist + Content-Sanity-Check
- Liefert einen strukturierten Report pro Projekt
- Kein Auto-Cleanup. Entfernung erfolgt durch `scripts/dedupe_legacy_feedback.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


# Allowlist: Dateinamen die wir als Core-Policy-Duplikate klassifizieren duerfen.
# Schluessel = exakter Dateiname im memory/-Dir. Wert = Keywords, von denen mind.
# eins im Inhalt stehen muss, damit wir die Datei wirklich als Duplikat markieren
# (schuetzt User die evtl. einen gleichnamigen File mit anderem Inhalt pflegen).
LEGACY_CORE_POLICY_FEEDBACK_FILES: dict[str, tuple[str, ...]] = {
    "feedback_token_discipline.md":        ("token", "budget"),
    "feedback_memory_budget.md":           ("memory", "budget"),
    "feedback_bulk_lookups.md":            ("bulk", "parallel", "lookup"),
    "feedback_grep_before_read.md":        ("grep", "read", "search"),
    "feedback_file_offset_limit.md":       ("offset", "limit", "has_more"),
    "feedback_patch_instead_of_describe.md": ("patch", "describe", "change"),
    "feedback_memory_path.md":             ("memory", "path", "projects"),
    "feedback_read_memory_index_first.md": ("index", "MEMORY.md", "read_memory"),
    "feedback_write_memory_actively.md":   ("write_memory", "frontmatter"),
    "feedback_github_for_hydrahive.md":    ("github", "hydrahive", "gitea"),
    "feedback_short_answers.md":           ("kurz", "terse", "short", "antwort"),
    "feedback_match_user_language.md":     ("language", "sprache", "deutsch", "english"),
    "feedback_ask_when_unsure.md":         ("nachfragen", "unsicher", "ask", "unsure"),
}


@dataclass
class LegacyFileHit:
    """Ein Fund: Datei existiert und Content matcht Sanity-Check."""
    project_id: str
    path: Path
    size_bytes: int
    keyword_match: bool  # False = Name matcht Allowlist, aber Content hat keinen erwarteten Token
    matched_keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "project_id":       self.project_id,
            "path":             str(self.path),
            "size_bytes":       self.size_bytes,
            "keyword_match":    self.keyword_match,
            "matched_keywords": self.matched_keywords,
        }


def _scan_project(project_id: str, memory_dir: Path) -> list[LegacyFileHit]:
    hits: list[LegacyFileHit] = []
    if not memory_dir.is_dir():
        return hits
    for filename, keywords in LEGACY_CORE_POLICY_FEEDBACK_FILES.items():
        candidate = memory_dir / filename
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace").lower()
            size = candidate.stat().st_size
        except OSError:
            continue
        matched = [kw for kw in keywords if kw.lower() in text]
        hits.append(LegacyFileHit(
            project_id=project_id,
            path=candidate,
            size_bytes=size,
            keyword_match=bool(matched),
            matched_keywords=matched,
        ))
    return hits


def scan_legacy_feedback(projects_dir: Path) -> list[LegacyFileHit]:
    """Scannt alle Projekt-Memories unter projects_dir nach Legacy-Feedback-Dateien.

    Uebersprungt:
    - `_deleted_*`-Verzeichnisse (soft-deletes)
    - Projekte ohne `memory/`-Unterverzeichnis
    """
    projects_dir = Path(projects_dir)
    if not projects_dir.is_dir():
        return []
    all_hits: list[LegacyFileHit] = []
    for entry in sorted(projects_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_deleted_"):
            continue
        all_hits.extend(_scan_project(entry.name, entry / "memory"))
    return all_hits


def summarize_report(hits: list[LegacyFileHit]) -> dict:
    """Ergebnis fuer API-Response: kompakt aggregiert + voller Detail-Array."""
    per_project: dict[str, int] = {}
    safe_to_remove = 0
    uncertain = 0
    for h in hits:
        per_project[h.project_id] = per_project.get(h.project_id, 0) + 1
        if h.keyword_match:
            safe_to_remove += 1
        else:
            uncertain += 1
    return {
        "total_files":          len(hits),
        "safe_to_remove":       safe_to_remove,
        "uncertain_content":    uncertain,
        "projects_affected":    len(per_project),
        "per_project_count":    per_project,
        "files":                [h.to_dict() for h in hits],
    }
