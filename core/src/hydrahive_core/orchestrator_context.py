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
from .memory_search import search_memory, update_index as update_memory_index
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


async def _build_system_prompt(boss_cfg, user_text: str) -> str:
    mode  = _context_mode(user_text)
    parts = [f"Du bist {boss_cfg.identity}."]

    # Soul laden wenn vorhanden (immer — klein und identitätskritisch)
    if boss_cfg.soul and boss_cfg.agent_dir:
        soul_path = boss_cfg.agent_dir / boss_cfg.soul
        if soul_path.exists():
            parts.append(soul_path.read_text(encoding="utf-8").strip())

    # Persistentes Gedächtnis — BM25 Memory Search (OpenClaw-Stil, kein GPU)
    if boss_cfg.agent_dir:
        mem_parts = []

        # Learning-Snippet (bleibt wie bisher — schon kompakt)
        memory_dir = boss_cfg.agent_dir / "memory"
        if memory_dir.exists():
            learning_snippet = build_learning_prompt_snippet(
                boss_cfg.agent_dir,
                **({"max_entries": 12, "max_chars": 4096} if mode == "full"
                   else {"max_entries": 5, "max_chars": 2048}),
            )
            if learning_snippet:
                mem_parts.append(learning_snippet)

        # Index aktualisieren (lazy — nur geänderte Dateien, <5ms wenn nichts geändert)
        update_memory_index(boss_cfg.agent_dir)

        # BM25-Suche: normal=6, full=12 Treffer × max 700 chars ≈ 4-8k chars
        k = 12 if mode == "full" else 6
        snippets = search_memory(boss_cfg.agent_dir, user_text, k=k)

        if snippets:
            mem_parts.append("### Erinnerungen\n" + "\n---\n".join(snippets))

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


def _flush_summary_to_memory(agent_dir, summary: str) -> None:
    """Memory Flush (OpenClaw-Stil): Kompaktierungs-Summary in tagesaktuelle
    Memory-Datei schreiben, damit zukünftige BM25-Suchen relevante Fakten finden.
    Append-only — bestehende Einträge werden nie überschrieben.
    """
    import datetime
    memory_dir = agent_dir / "memory"
    try:
        memory_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.date.today().isoformat()
        flush_file = memory_dir / f"session-summary-{today}.md"
        timestamp = datetime.datetime.now().strftime("%H:%M")
        entry = f"\n## Session Summary {timestamp}\n\n{summary.strip()}\n"
        with flush_file.open("a", encoding="utf-8") as fh:
            fh.write(entry)
        logger.debug("Memory Flush: Summary in %s geschrieben", flush_file.name)
    except OSError as e:
        logger.warning("Memory Flush fehlgeschlagen: %s", e)


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
            # Memory Flush (OpenClaw-Stil): Summary in Memory-Datei schreiben
            # damit zukünftige Sessions relevante Fakten via BM25 finden
            if boss_cfg.agent_dir:
                _flush_summary_to_memory(boss_cfg.agent_dir, summary)
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
