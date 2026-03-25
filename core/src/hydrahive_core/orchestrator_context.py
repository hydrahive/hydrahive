"""
orchestrator_context.py — Context-Compaction & Memory-Budget

Standalone-Funktionen für System-Prompt-Aufbau und Context-Kompaktierung:
- _context_mode: normal vs. full anhand der User-Nachricht
- _build_system_prompt: Soul + Memory + Skills zusammenbauen (mit Budget-Limit)
- _repo_review_guidance: Repo-Review-Arbeitsrahmen einblenden
- _compact_if_needed: veralteten Kontext per LLM zusammenfassen
"""
from __future__ import annotations

import logging

import litellm

from .learning_memory import build_learning_prompt_snippet
from .skill_loader import load_skills, select_skills, skills_to_system_prompt

logger = logging.getLogger(__name__)


def _context_mode(user_text: str) -> str:
    """
    Bestimmt den Kontext-Modus anhand des Inhalts der User-Nachricht.

    normal  — Standard: kompakter Kontext (5 Learning-Einträge, max 5 Memory-Dateien)
    full    — Vollständiger Kontext: alle Memory-Dateien, 12 Learning-Einträge

    full wird ausgelöst bei expliziten Repo-/Audit-/Deep-Dive-Anfragen —
    Trigger-Wörter sind bewusst eng gewählt, um unnötige full-Aufrufe zu vermeiden.
    """
    text = (user_text or "").lower()
    full_triggers = (
        "diff ", "diff\n", "patch", "pull request", " pr #", " pr:",
        "audit", "deep dive", "deep-dive", "vollständig", "alles zeigen",
        "review mein", "review den", "review die", "analysiere alles",
        "zeig mir alle", "komplett analysier",
    )
    return "full" if any(t in text for t in full_triggers) else "normal"


def _build_system_prompt(boss_cfg, user_text: str) -> str:
    mode  = _context_mode(user_text)
    parts = [f"Du bist {boss_cfg.identity}."]

    # Soul laden wenn vorhanden (immer — klein und identitätskritisch)
    if boss_cfg.soul and boss_cfg.agent_dir:
        soul_path = boss_cfg.agent_dir / boss_cfg.soul
        if soul_path.exists():
            parts.append(soul_path.read_text(encoding="utf-8").strip())

    # Persistentes Gedächtnis laden (#85)
    if boss_cfg.agent_dir:
        memory_dir = boss_cfg.agent_dir / "memory"
        if memory_dir.exists():
            mem_parts = []

            # Learning-Snippet: normal=5/2048, full=12/4096
            if mode == "full":
                learning_snippet = build_learning_prompt_snippet(boss_cfg.agent_dir)
            else:
                learning_snippet = build_learning_prompt_snippet(
                    boss_cfg.agent_dir, max_entries=5, max_chars=2048
                )
            if learning_snippet:
                mem_parts.append(learning_snippet)

            # Memory-Dateien: neueste zuerst, mit hartem Budget-Limit
            # (OpenClaw-Ansatz: conservative estimation, newest-first retention)
            # normal: max 5 Dateien, 8k chars/Datei, 30k chars gesamt
            # full:   max 15 Dateien, 25k chars/Datei, 150k chars gesamt
            if mode == "full":
                max_mem_files, per_file_chars, total_mem_chars = 15, 25_000, 150_000
            else:
                max_mem_files, per_file_chars, total_mem_chars = 5, 8_000, 30_000

            mem_files = sorted(
                (mf for mf in memory_dir.glob("*.md") if mf.name != "learned-facts.md"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:max_mem_files]

            mem_budget_used = 0
            for mf in mem_files:
                if mem_budget_used >= total_mem_chars:
                    break
                try:
                    text = mf.read_text(encoding="utf-8").strip()
                    if not text:
                        continue
                    if len(text) > per_file_chars:
                        text = text[:per_file_chars] + "\n…[gekürzt]"
                    remaining = total_mem_chars - mem_budget_used
                    if len(text) > remaining:
                        text = text[:remaining] + "\n…[Budget erschöpft]"
                    mem_parts.append(f"### {mf.stem}\n{text}")
                    mem_budget_used += len(text)
                except OSError:
                    pass
            if mem_parts:
                parts.append("## Persistentes Gedächtnis\n\n" + "\n\n".join(mem_parts))

    # QMD-Skills laden (scope=always immer, on-demand bei Keyword-Match)
    if boss_cfg.agent_dir:
        all_skills    = load_skills(boss_cfg.agent_dir)
        active_skills = select_skills(all_skills, user_text)
        if active_skills:
            parts.append(skills_to_system_prompt(active_skills))

    repo_guidance = _repo_review_guidance(boss_cfg, user_text)
    if repo_guidance:
        parts.append(repo_guidance)

    logger.debug("context-mode=%s agent=%s", mode, boss_cfg.id)
    return "\n\n".join(parts)


def _repo_review_guidance(agent_cfg, user_text: str) -> str:
    text = (user_text or "").lower()
    triggers = (
        "repo", "repository", "review", "commit", "diff", "issue",
        "gitea", "github", "pull request", "pr ", "datei", "file",
        "struktur", "tree", "deep dive", "http://", "https://",
    )
    if not any(token in text for token in triggers):
        return ""

    available  = set(agent_cfg.tools or [])
    repo_tools = {"gitea_repo_inspect", "gitea_repo_tree", "gitea_repo_file", "gitea_repo_commits"}
    if not available.intersection(repo_tools) and "git_status" not in available and "git_diff" not in available:
        return ""

    return (
        "## Repo-Review-Arbeitsrahmen\n"
        "- Bei Repo-, Review-, Commit- oder Datei-Anfragen zuerst das Zielrepo sauber auflösen.\n"
        "- Für Gitea-Repo-Links repo-aware Tools bevorzugen, nicht mit einem rohen http_request nach dem ersten 404 aufhören.\n"
        "- Sinnvolle Reihenfolge:\n"
        "  1. gitea_repo_inspect für Repo-Metadaten und Grundzustand\n"
        "  2. gitea_repo_tree für Struktur und relevante Verzeichnisse\n"
        "  3. gitea_repo_file für konkrete Dateien\n"
        "  4. git_status oder git_diff nur wenn lokaler Workspace-Zustand oder Änderungen wirklich relevant sind\n"
        "- Keine breite Bewertung ohne mindestens Struktur oder konkrete Dateien geprüft zu haben.\n"
        "- Wenn ein Repo-Link nicht direkt öffnet, über Repo-Auflösung, API oder owner/repo weiterarbeiten statt abzubrechen."
    )


async def _compact_if_needed(
    sessions,
    project_id: str,
    boss_cfg,
    *,
    keep_last: int = 4,
) -> None:
    """
    Context-Kompaktierung (#74): wenn Session zu gross wird, älteren Kontext
    per LLM zusammenfassen und durch eine Summary-Message ersetzen.
    Threshold ist model-aware: Claude/GPT-4/Gemini/Mistral-Large = 20000 Tokens
    (estimated_tokens() unterschätzt echte Tokens um ~5x, daher konservativ),
    lokale/kleine Modelle = 2000 Tokens.
    """
    from .orchestrator_llm import _llm_with_retry

    model = boss_cfg.llm.model.lower()
    if any(x in model for x in ("claude", "gpt-4", "gpt-3.5", "gemini", "mistral-large")):
        token_threshold = 20_000
    else:
        token_threshold = 2_000  # lokale Modelle haben kleine Kontextfenster

    if sessions.estimated_tokens(project_id) < token_threshold:
        return

    session = sessions.get_active(project_id)
    if not session or len(session.messages) <= keep_last + 2:
        return

    to_summarize = session.messages[:-keep_last]
    history_text = "\n".join(
        f"{m.role.value.upper()}: {m.content[:500]}"
        for m in to_summarize
    )

    summary_prompt = [
        {"role": "system", "content": (
            "Fasse die folgende Konversation prägnant zusammen. "
            "Behalte alle wichtigen Fakten, Entscheidungen und Aufgaben. "
            "Antworte nur mit der Zusammenfassung, keine Einleitung."
        )},
        {"role": "user", "content": history_text},
    ]

    try:
        resp = await _llm_with_retry(lambda: litellm.acompletion(
            model=boss_cfg.llm.model,
            messages=summary_prompt,
            max_tokens=400,
            drop_params=True,
        ))
        summary = resp.choices[0].message.content or ""
        if summary:
            await sessions.compact(project_id, summary, keep_last=keep_last)
            logger.info(
                "Context kompaktiert (Projekt: %s, ~%d Tokens → Summary)",
                project_id, sessions.estimated_tokens(project_id),
            )
    except Exception as e:
        logger.warning("Context-Kompaktierung fehlgeschlagen: %s", e)
        # Notfall-Reset: wenn Session zu gross ist um kompaktiert zu werden
        current_tokens = sessions.estimated_tokens(project_id)
        if current_tokens > 30_000:
            logger.error(
                "Context-Notfall-Reset (Projekt: %s, ~%d geschätzte Tokens > 30k) — "
                "Session wird geleert um Token-Overflow zu verhindern",
                project_id, current_tokens,
            )
            await sessions.new_session(project_id)
