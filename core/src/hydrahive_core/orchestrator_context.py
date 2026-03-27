"""
orchestrator_context.py — Context-Compaction & Memory-Budget

Standalone-Funktionen für System-Prompt-Aufbau und Context-Kompaktierung:
- _context_mode: normal vs. full anhand der User-Nachricht
- _build_system_prompt: Soul + Memory + Skills zusammenbauen (mit Budget-Limit)
- _repo_review_guidance: Repo-Review-Arbeitsrahmen einblenden
- _compact_if_needed: veralteten Kontext per LLM zusammenfassen
"""
from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

import litellm

from .learning_memory import build_learning_prompt_snippet
from .memory_search import search_memory, update_index as update_memory_index
from .skill_loader import load_skills, select_skills, skills_to_system_prompt, Skill

logger = logging.getLogger(__name__)

# Python-seitiger System-Prompt-Cache (ergänzt Anthropic Server-Side-Caching)
# Format: agent_id → (prompt_str, timestamp, cache_hash)
_PROMPT_CACHE: dict[str, tuple[str, float, str]] = {}
_PROMPT_CACHE_TTL = 300  # 5 Min — gleich wie Anthropic ephemeral cache

# Kontextfenster je Modell-Familie (Tokens)
_MODEL_CONTEXT_TOKENS: dict[str, int] = {
    "claude":   200_000,
    "gpt-4o":   128_000,
    "gpt-4":    128_000,
    "gpt-3.5":   16_000,
    "gemini":   128_000,
    "mistral":   32_000,
}
_MAX_HISTORY_SHARE = 0.30  # max 30% des Kontextfensters für History (OpenClaw-Stil)


def _history_token_budget(model: str) -> int:
    """Maximale Token-Anzahl für die Message-History (30% des Modell-Kontextfensters).

    Gibt einen konservativen Wert für unbekannte Modelle zurück (8k × 30% = 2400 Tokens).
    """
    model_lower = (model or "").lower()
    for key, ctx_tokens in _MODEL_CONTEXT_TOKENS.items():
        if key in model_lower:
            return int(ctx_tokens * _MAX_HISTORY_SHARE)
    return int(8_000 * _MAX_HISTORY_SHARE)  # Fallback für lokale/unbekannte Modelle


def _prompt_cache_hash(agent_dir: Path, mode: str) -> str:
    """Hash über alle Faktoren die den System-Prompt beeinflussen."""
    parts = [mode]
    soul = agent_dir / "soul.md"
    if soul.exists():
        parts.append(f"soul:{soul.stat().st_mtime:.0f}")
    memory_dir = agent_dir / "memory"
    if memory_dir.exists():
        for f in sorted(memory_dir.glob("*.md")):
            parts.append(f"{f.name}:{f.stat().st_mtime:.0f}")
    skills_dir = agent_dir / "skills"
    if skills_dir.exists():
        for f in sorted(skills_dir.glob("*.md")):
            parts.append(f"{f.name}:{f.stat().st_mtime:.0f}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _context_mode(user_text: str) -> str:
    """
    Bestimmt den Kontext-Modus anhand des Inhalts der User-Nachricht.

    normal  — Standard: kompakter Kontext (5 Learning-Einträge, k=4 BM25-Snippets)
    full    — Erweiterter Kontext: 12 Learning-Einträge, k=8 BM25-Snippets

    full nur bei explizitem Prefix "!full" oder klar intentionalen Deep-Dive-Phrasen.
    Einzelne Wörter wie "diff", "audit", "patch" reichen NICHT — zu viele False Positives.
    """
    text = (user_text or "").lower()
    # Expliziter Opt-in
    if text.startswith("!full"):
        return "full"
    # Nur bei klar intentionalen Phrasen (keine Einzelwörter)
    full_triggers = (
        "deep dive", "deep-dive",
        "analysiere alles", "zeig mir alles",
        "komplett analysier", "vollständiger kontext",
        "full context",
    )
    return "full" if any(t in text for t in full_triggers) else "normal"


async def _build_system_prompt(boss_cfg, user_text: str, *, invalidate: bool = False) -> str:
    """Baut den System-Prompt — mit Python-Cache (5 Min TTL, hash-basiert)."""
    mode = _context_mode(user_text)

    if not invalidate and boss_cfg.agent_dir:
        cached = _PROMPT_CACHE.get(boss_cfg.id)
        if cached:
            prompt, ts, h = cached
            if (time.time() - ts) < _PROMPT_CACHE_TTL:
                current_h = _prompt_cache_hash(boss_cfg.agent_dir, mode)
                if current_h == h:
                    logger.debug("system-prompt cache-hit (agent=%s age=%.0fs)", boss_cfg.id, time.time() - ts)
                    return prompt
        logger.debug("system-prompt cache-miss (agent=%s) — rebuilding", boss_cfg.id)

    parts = [f"Du bist {boss_cfg.identity}."]

    # startup.md — Erster Start / Onboarding
    # VOR soul.md injiziert damit Onboarding-Instruktionen die normale Persönlichkeit überschreiben.
    # Existiert die Datei → wird injiziert. Agent löscht sie selbst nach Abschluss.
    _startup_active = False
    if boss_cfg.agent_dir:
        startup_path = boss_cfg.agent_dir / "startup.md"
        if startup_path.exists():
            startup_text = startup_path.read_text(encoding="utf-8").strip()
            if startup_text:
                parts.append(
                    f"## ERSTER START — ONBOARDING\n\n"
                    f"**WICHTIG: Diese Anweisung hat höchste Priorität und überschreibt alle anderen "
                    f"Persönlichkeits- oder Verhaltensregeln aus der soul.md für diesen ersten Start.**\n\n"
                    f"{startup_text}"
                )
                _startup_active = True

    # Soul laden wenn vorhanden (immer — klein und identitätskritisch)
    # Bei aktivem Onboarding trotzdem laden (für Kontext), aber startup.md hat Vorrang.
    if boss_cfg.soul and boss_cfg.agent_dir:
        soul_path = boss_cfg.agent_dir / boss_cfg.soul
        if soul_path.exists():
            parts.append(soul_path.read_text(encoding="utf-8").strip())

    # Persistentes Gedächtnis — BM25 Memory Search (OpenClaw-Stil, kein GPU)
    if boss_cfg.agent_dir:
        mem_parts = []
        memory_dir = boss_cfg.agent_dir / "memory"

        # INDEX.md — Vault-Pattern (OpenClaw boot-md Äquivalent):
        # Immer direkt geladen (nicht via BM25), max 1500 chars.
        # Agent hält diese Datei slim (Inhaltsverzeichnis / Kernfakten).
        if memory_dir.exists():
            index_path = memory_dir / "INDEX.md"
            if index_path.exists():
                index_text = index_path.read_text(encoding="utf-8").strip()
                if index_text:
                    if len(index_text) > 1500:
                        index_text = index_text[:1500] + "\n…[INDEX.md gekürzt]"
                    mem_parts.append(f"### Index\n{index_text}")

        # Learning-Snippet (bleibt wie bisher — schon kompakt)
        if memory_dir.exists():
            learning_snippet = build_learning_prompt_snippet(
                boss_cfg.agent_dir,
                **({"max_entries": 8, "max_chars": 3000} if mode == "full"
                   else {"max_entries": 3, "max_chars": 1500}),
            )
            if learning_snippet:
                mem_parts.append(learning_snippet)

        # Index aktualisieren (lazy — nur geänderte Dateien, <5ms wenn nichts geändert)
        update_memory_index(boss_cfg.agent_dir)

        # BM25-Suche: normal=4, full=8 Treffer × max 700 chars ≈ 2.8-5.6k chars
        k = 8 if mode == "full" else 4
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
    prompt = "\n\n".join(parts)

    # In Python-Cache speichern
    if boss_cfg.agent_dir:
        h = _prompt_cache_hash(boss_cfg.agent_dir, mode)
        _PROMPT_CACHE[boss_cfg.id] = (prompt, time.time(), h)

    return prompt


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


def get_skill_tool_constraints(boss_cfg, user_text: str) -> tuple[list[str], list[str]]:
    """
    Gibt (allowed_tools, blocked_tools) der aktiven Skills zurück.
    Kombinationsregel über mehrere aktive Skills:
    - allowed_tools: Vereinigung (jeder Skill kann Tools freischalten)
    - blocked_tools: Vereinigung (jeder Skill kann Tools sperren)
    - blocked_tools gewinnt bei Konflikt
    Wenn kein aktiver Skill Tool-Constraints hat → leere Listen (keine Einschränkung).
    """
    if not boss_cfg.agent_dir:
        return [], []
    all_skills = load_skills(boss_cfg.agent_dir)
    active = select_skills(all_skills, user_text)
    combined_allowed: set[str] = set()
    combined_blocked: set[str] = set()
    has_allowed_constraint = False
    for skill in active:
        if skill.allowed_tools:
            has_allowed_constraint = True
            combined_allowed.update(skill.allowed_tools)
        combined_blocked.update(skill.blocked_tools)
    allowed = list(combined_allowed) if has_allowed_constraint else []
    return allowed, list(combined_blocked)


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
        token_threshold = 4_000   # estimated_tokens unterschätzt ~5x → real ~20k
    else:
        token_threshold = 1_000  # lokale Modelle haben kleine Kontextfenster

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
        if current_tokens > 15_000:
            logger.error(
                "Context-Notfall-Reset (Projekt: %s, ~%d geschätzte Tokens > 15k) — "
                "Session wird geleert um Token-Overflow zu verhindern",
                project_id, current_tokens,
            )
            await sessions.new_session(project_id)
