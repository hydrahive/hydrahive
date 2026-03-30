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


def invalidate_prompt_cache(agent_id: str) -> None:
    """Löscht den gecachten System-Prompt für einen Agenten (z.B. nach Blueprint-Änderung)."""
    _PROMPT_CACHE.pop(agent_id, None)

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
    handbook = Path("/etc/hydrahive/system_handbook.md")
    if handbook.exists():
        parts.append(f"handbook:{handbook.stat().st_mtime:.0f}")
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

    # Agenten-Quellen — URLs/Suchmaschinen die diesem Agenten zugewiesen sind
    if getattr(boss_cfg, "sources", None):
        src_lines = []
        for src in boss_cfg.sources:
            line = f"- **{src.name}**: {src.url}"
            if src.description:
                line += f" — {src.description}"
            src_lines.append(line)
        parts.append(
            "## Zugewiesene Quellen & Suchmaschinen\n\n"
            "Nutze diese Quellen wenn du Informationen zu deinem Fachgebiet benötigst. "
            "Rufe relevante Quellen mit `http_request` ab bevor du antwortest — "
            "zitiere niemals aus dem Gedächtnis wenn eine Quelle verfügbar ist.\n\n"
            + "\n".join(src_lines)
        )

    # System-Handbuch — globale Arbeitsweise, wird in jeden Agenten injiziert
    _handbook_path = Path("/etc/hydrahive/system_handbook.md")
    if _handbook_path.exists():
        _handbook_text = _handbook_path.read_text(encoding="utf-8").strip()
        if _handbook_text:
            parts.append(_handbook_text)

    # QMD-Skills laden (scope=always immer, on-demand bei Keyword-Match)
    if boss_cfg.agent_dir:
        all_skills    = load_skills(boss_cfg.agent_dir)
        active_skills = select_skills(all_skills, user_text)
        if active_skills:
            parts.append(skills_to_system_prompt(active_skills))

    # Agent-Blueprint Kontext (workflow_blueprint.json)
    if boss_cfg.agent_dir:
        blueprint_ctx = _load_agent_blueprint_context(boss_cfg.agent_dir)
        if blueprint_ctx:
            parts.append(blueprint_ctx)

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


def _load_agent_blueprint_context(agent_dir) -> str:
    """
    Liest workflow_blueprint.json aus dem Agent-Verzeichnis und serialisiert
    Repos, Credentials, Skills, Memory und ToolPolicies als Kontext-Block.
    """
    import json as _json

    wf_path = Path(agent_dir) / "workflow_blueprint.json"
    if not wf_path.exists():
        return ""
    try:
        wf = _json.loads(wf_path.read_text(encoding="utf-8"))
    except Exception:
        return ""

    nodes: list[dict] = wf.get("nodes", [])
    if not nodes:
        return ""

    repos, creds, skills_bp, memory_bp, policies = [], [], [], [], []
    for node in nodes:
        ntype = node.get("type", "")
        d     = node.get("data", {})
        cfg   = d.get("config", {})
        label = d.get("label", "")
        if ntype == "repository":
            url    = cfg.get("url", "")
            branch = cfg.get("branch", "main")
            path   = cfg.get("path", "/")
            repos.append(f"- **{label}**: `{url}` (Branch: {branch}, Pfad: {path})")
        elif ntype == "credential":
            key    = cfg.get("key", label)
            source = cfg.get("source", "config")
            creds.append(f"- **{label}**: Key `{key}` (Quelle: {source})")
        elif ntype == "skill":
            file_ = cfg.get("file", label)
            skills_bp.append(f"- {label} (`{file_}`)")
        elif ntype == "memory":
            file_ = cfg.get("file", label)
            always = cfg.get("always", False)
            memory_bp.append(f"- {label} (`{file_}`)" + (" — immer geladen" if always else ""))
        elif ntype == "toolpolicy":
            tool    = cfg.get("tool", label)
            allowed = cfg.get("allowed", True)
            note    = cfg.get("note", "")
            status  = "✓ erlaubt" if allowed else "✗ gesperrt"
            policies.append(f"- `{tool}`: {status}" + (f" — {note}" if note else ""))

    parts = ["## Agent-Konfiguration (Blueprint)"]
    if repos:
        parts.append("### Repositories\n" + "\n".join(repos))
        parts.append("→ Nutze `gitea_repo_inspect`, `gitea_repo_tree`, `gitea_repo_file` oder `http_request` um auf diese Repositories zuzugreifen.")
    if creds:
        parts.append("### Verfügbare Credentials\n" + "\n".join(creds))
    if skills_bp:
        parts.append("### Zugewiesene Skills\n" + "\n".join(skills_bp))
    if memory_bp:
        parts.append("### Pinned Memory\n" + "\n".join(memory_bp))
    if policies:
        parts.append("### Tool-Policy\n" + "\n".join(policies))

    if len(parts) == 1:
        return ""  # Nur Überschrift, keine Inhalte
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
    keep_last: int = 6,
) -> None:
    """
    Mehrstufige Context-Kompaktierung (#47).

    Stufe 1 — Rolling Summary:
      Wenn estimated_tokens > token_threshold: die ältesten Nachrichten (alles
      außer den letzten keep_last) werden per LLM zusammengefasst.
      Eine bereits vorhandene Summary-Message wird dabei als Vorwissen
      in den neuen Zusammenfassungs-Prompt eingebaut (Rolling-Kette).

    Stufe 2 — Meta-Summary:
      Wenn nach Stufe 1 die Session immer noch > token_threshold ist
      (z. B. keep_last-Nachrichten selbst sehr groß), wird die neue Summary
      nochmal auf max 300 Token verdichtet.

    Threshold ist model-aware:
      Claude/GPT-4/Gemini/Mistral-Large → 4000 estimated (~20k real)
      Lokale/kleine Modelle             → 1000 estimated (~5k real)
    """
    from .orchestrator_llm import _llm_with_retry
    from .session_manager import MessageRole

    model = boss_cfg.llm.model.lower()
    if any(x in model for x in ("claude", "gpt-4", "gpt-3.5", "gemini", "mistral-large", "openai-codex", "gpt-5")):
        token_threshold = 4_000
    else:
        token_threshold = 1_000

    # openai-codex/ ist ein Custom-Provider — litellm kennt ihn nicht.
    # Für Kompaktierung auf Claude Haiku fallbacken.
    compact_model = boss_cfg.llm.model
    if compact_model.startswith("openai-codex/"):
        compact_model = "claude-haiku-4-5-20251001"

    if sessions.estimated_tokens(project_id) < token_threshold:
        return

    session = sessions.get_active(project_id)
    if not session or len(session.messages) < 4:
        return

    # Vorhandene Summary-Message (Stufe-1-Kette) extrahieren
    existing_summary = ""
    msgs = session.messages
    if msgs and msgs[0].role == MessageRole.SYSTEM and msgs[0].content.startswith("[Zusammenfassung"):
        existing_summary = msgs[0].content
        msgs = msgs[1:]

    # Nachrichten die kompaktiert werden (alles außer den letzten keep_last)
    to_summarize = msgs[:-keep_last] if len(msgs) > keep_last else msgs[:]
    if not to_summarize and not existing_summary:
        return

    history_lines = "\n".join(
        f"{m.role.value.upper()}: {m.content[:1500]}"
        for m in to_summarize
    )

    if existing_summary:
        user_content = (
            f"BISHERIGE ZUSAMMENFASSUNG:\n{existing_summary}\n\n"
            f"NEUE NACHRICHTEN:\n{history_lines}"
        )
        system_instruction = (
            "Du bekommst eine bisherige Zusammenfassung plus neue Nachrichten. "
            "Erstelle eine aktualisierte, vollständige Zusammenfassung. "
            "Behalte alle wichtigen Fakten, Entscheidungen und offenen Aufgaben. "
            "Antworte nur mit der Zusammenfassung, keine Einleitung."
        )
    else:
        user_content = history_lines
        system_instruction = (
            "Fasse die folgende Konversation prägnant zusammen. "
            "Behalte alle wichtigen Fakten, Entscheidungen und Aufgaben. "
            "Antworte nur mit der Zusammenfassung, keine Einleitung."
        )

    summary_prompt = [
        {"role": "system", "content": system_instruction},
        {"role": "user",   "content": user_content},
    ]

    try:
        resp = await _llm_with_retry(lambda: litellm.acompletion(
            model=compact_model,
            messages=summary_prompt,
            max_tokens=700,
            drop_params=True,
        ))
        summary = resp.choices[0].message.content or ""
        if not summary:
            return

        await sessions.compact(project_id, summary, keep_last=keep_last)
        logger.info(
            "Context kompaktiert Stufe-1 (Projekt: %s, ~%d Tokens nach Kompaktierung)",
            project_id, sessions.estimated_tokens(project_id),
        )

        # Stufe 2: wenn immer noch zu groß, Summary selbst verdichten
        if sessions.estimated_tokens(project_id) >= token_threshold and len(summary) > 400:
            meta_prompt = [
                {"role": "system", "content": (
                    "Verdichte die folgende Zusammenfassung auf das Wesentlichste. "
                    "Maximal 250 Wörter. Nur die Zusammenfassung, keine Einleitung."
                )},
                {"role": "user", "content": summary},
            ]
            resp2 = await _llm_with_retry(lambda: litellm.acompletion(
                model=compact_model,
                messages=meta_prompt,
                max_tokens=350,
                drop_params=True,
            ))
            meta = resp2.choices[0].message.content or ""
            if meta:
                await sessions.compact(project_id, meta, keep_last=keep_last)
                summary = meta
                logger.info(
                    "Context kompaktiert Stufe-2 (Projekt: %s, ~%d Tokens nach Meta-Summary)",
                    project_id, sessions.estimated_tokens(project_id),
                )

        # Memory Flush: Summary in Memory-Datei schreiben
        # damit zukünftige Sessions relevante Fakten via BM25 finden
        if boss_cfg.agent_dir:
            _flush_summary_to_memory(boss_cfg.agent_dir, summary)

    except Exception as e:
        logger.warning("Context-Kompaktierung fehlgeschlagen: %s", e)
        current_tokens = sessions.estimated_tokens(project_id)
        if current_tokens > 15_000:
            logger.error(
                "Context-Notfall-Reset (Projekt: %s, ~%d geschätzte Tokens > 15k)",
                project_id, current_tokens,
            )
            await sessions.new_session(project_id)
