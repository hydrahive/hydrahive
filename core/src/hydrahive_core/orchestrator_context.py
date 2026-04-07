"""
orchestrator_context.py — Context-Compaction & Memory-Budget

Standalone-Funktionen für System-Prompt-Aufbau und Context-Kompaktierung:
- _context_mode: normal vs. full anhand der User-Nachricht
- _build_system_prompt: Soul + Memory + Skills zusammenbauen (mit Budget-Limit)
- _repo_review_guidance: Repo-Review-Arbeitsrahmen einblenden
- _compact_if_needed: veralteten Kontext per LLM zusammenfassen
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from pathlib import Path

import litellm

from .learning_memory import build_learning_prompt_snippet
from .memory_search import search_memory, update_index as update_memory_index
from .semantic_index import score_texts
from .settings import settings
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
_RESERVE_TOKENS_FLOOR = 20_000  # Immer 20k frei für Response (OpenClaw: reserveTokensFloor)


def _context_window_for_model(model: str) -> int:
    """Context-Window-Größe für ein Modell."""
    model_lower = (model or "").lower()
    for key, ctx_tokens in _MODEL_CONTEXT_TOKENS.items():
        if key in model_lower:
            return ctx_tokens
    return 8_000  # Fallback für lokale/unbekannte Modelle


from .token_estimation import estimate_tokens as _estimate_tokens


def _history_token_budget(model: str, system_prompt_tokens: int = 0) -> int:
    """Maximale Token-Anzahl für die Message-History.

    OpenClaw-Formel:
      verfügbar = context_window - system_prompt - reserveTokensFloor
      history_budget = verfügbar × maxHistoryShare

    System-Prompt wird abgezogen damit History nicht verdrängt wird.
    """
    ctx = _context_window_for_model(model)
    available = ctx - system_prompt_tokens - _RESERVE_TOKENS_FLOOR
    if available < 2000:
        available = 2000  # Minimum damit Agent überhaupt antworten kann
    return int(available * _MAX_HISTORY_SHARE)


def _prompt_cache_hash(agent_dir: Path, mode: str) -> str:
    """Hash über alle Faktoren die den System-Prompt beeinflussen."""
    parts = [mode]
    handbook = settings.system_handbook
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
    wf_flow = agent_dir / "workflow_flow.json"
    if wf_flow.exists():
        parts.append(f"workflow_flow:{wf_flow.stat().st_mtime:.0f}")
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
        # run_in_executor: SQLite + FAISS/Embedding sind blocking I/O, darf Event-Loop nicht blockieren
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, update_memory_index, boss_cfg.agent_dir)

        # BM25-Suche: normal=4, full=8 Treffer × max 700 chars ≈ 2.8-5.6k chars
        k = 8 if mode == "full" else 4
        snippets = await loop.run_in_executor(
            None, lambda: search_memory(boss_cfg.agent_dir, user_text, k=k)
        )

        if snippets:
            mem_parts.append("### Erinnerungen\n" + "\n---\n".join(snippets))

        if mem_parts:
            parts.append("## Persistentes Gedächtnis\n\n" + "\n\n".join(mem_parts))

    # #350: Session-Continuity — letzte Session nach /clear automatisch injizieren
    if boss_cfg.agent_dir:
        last_session_path = boss_cfg.agent_dir / "memory" / "_last_session.md"
        if last_session_path.exists():
            import os
            # Nur injizieren wenn < 24h alt (stale prevention)
            age_hours = (time.time() - os.path.getmtime(last_session_path)) / 3600
            if age_hours < 24:
                last_text = last_session_path.read_text(encoding="utf-8").strip()
                if last_text:
                    if len(last_text) > 3000:
                        last_text = last_text[:3000] + "\n…[gekürzt]"
                    parts.append(
                        "## Letzte Session (vor Clear)\n\n"
                        "Dieser Kontext stammt aus der vorherigen Session. "
                        "Nutze ihn als Hintergrund falls der User darauf Bezug nimmt.\n\n"
                        + last_text
                    )

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

    # Zugewiesene Git-Repos — Credentials + Workflow-Info für Git-Tools
    try:
        from .repo_config import repos_for_agent
        agent_repos = repos_for_agent(boss_cfg.id)
        if agent_repos:
            repo_lines = []
            for repo in agent_repos:
                clone_url = repo.url
                if repo.token and "github.com" in repo.url:
                    clone_url = repo.url.replace("https://", f"https://{repo.token}@")
                repo_lines.append(
                    f"- **{repo.name}** ({repo.provider}): {repo.url}\n"
                    f"  Clone-URL (mit Token): `{clone_url}.git`\n"
                    f"  Branch: `{repo.branch}` | Token: vorhanden"
                )
            parts.append(
                "## Zugewiesene Git-Repos\n\n"
                "Diese Repos sind dir zugewiesen. Nutze die Clone-URL mit Token für git push/pull.\n"
                "Bei Anweisungen wie 'push' oder 'commit' nutze IMMER diese Repos — nicht nachfragen!\n\n"
                + "\n".join(repo_lines)
            )
    except Exception as e:
        logger.debug("Repo-Injection übersprungen: %s", e)

    # System-Handbuch — globale Arbeitsweise, wird in jeden Agenten injiziert
    _handbook_path = settings.system_handbook
    if _handbook_path.exists():
        _handbook_text = _handbook_path.read_text(encoding="utf-8").strip()
        if _handbook_text:
            parts.append(_handbook_text)

    # A-MEM Skills laden (scope=always immer, on-demand: Keyword-Match + Semantik #44)
    if boss_cfg.agent_dir:
        all_skills = load_skills(boss_cfg.agent_dir)
        # Semantische Scores berechnen (fällt auf {} zurück wenn FAISS nicht verfügbar)
        semantic_scores: dict[str, float] = {}
        if all_skills:
            skill_texts = [f"{s.skill} {' '.join(s.triggers)} {s.content[:300]}" for s in all_skills]
            raw_scores  = await loop.run_in_executor(None, score_texts, skill_texts, user_text)
            if raw_scores:
                semantic_scores = {s.skill: raw_scores[i] for i, s in enumerate(all_skills)}
        # Token-Budget: max 8000 Zeichen für Skills (~2k Tokens)
        active_skills = select_skills(all_skills, user_text, semantic_scores=semantic_scores)
        if active_skills:
            parts.append(skills_to_system_prompt(active_skills, token_budget=8000))

    # Agent-Blueprint Kontext (workflow_blueprint.json)
    if boss_cfg.agent_dir:
        blueprint_ctx = _load_agent_blueprint_context(boss_cfg.agent_dir)
        if blueprint_ctx:
            parts.append(blueprint_ctx)

    # Agent-Workflow (workflow_flow.json) — Arbeitsanweisung wie der Agent Aufgaben bearbeitet
    if boss_cfg.agent_dir:
        agent_wf = _load_agent_workflow_prompt(boss_cfg.agent_dir)
        if agent_wf:
            parts.append(agent_wf)

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
    except Exception as e:
        logger.debug("Failed to parse workflow blueprint %s: %s", wf_path, e)
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


def _load_agent_workflow_prompt(agent_dir) -> str:
    """
    Liest workflow_flow.json aus dem Agent-Verzeichnis und serialisiert es
    als Arbeitsanweisung für den System-Prompt.
    Analog zu _load_workflow_prompt() in orchestrator.py, aber agent-scoped.
    """
    import json as _json

    wf_path = Path(agent_dir) / "workflow_flow.json"
    if not wf_path.exists():
        return ""
    try:
        wf = _json.loads(wf_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug("Failed to parse workflow flow %s: %s", wf_path, e)
        return ""

    if not wf.get("enabled", True):
        return ""

    nodes: list[dict] = wf.get("nodes", [])
    edges: list[dict] = wf.get("edges", [])
    if not nodes:
        return ""

    # Topologische Reihenfolge via BFS ab Start-Nodes (ohne eingehende Edges)
    targets = {e["target"] for e in edges}
    start_ids = [n["id"] for n in nodes if n["id"] not in targets]
    if not start_ids:
        start_ids = [nodes[0]["id"]]

    ordered: list[dict] = []
    visited: set[str] = set()
    queue = list(start_ids)
    node_map = {n["id"]: n for n in nodes}
    edge_map: dict[str, list[dict]] = {}
    for e in edges:
        edge_map.setdefault(e["source"], []).append(e)

    while queue:
        nid = queue.pop(0)
        if nid in visited or nid not in node_map:
            continue
        visited.add(nid)
        ordered.append(node_map[nid])
        for e in edge_map.get(nid, []):
            if e["target"] not in visited:
                queue.append(e["target"])

    lines = [
        "## Arbeitsanweisung — Agent-Workflow",
        "Bearbeite Aufgaben IMMER nach folgendem Arbeitsablauf:",
        "",
    ]
    step_num = 1
    for node in ordered:
        ntype = node.get("type", "stepNode").replace("Node", "")
        data = node.get("data", {})
        label = data.get("label", "")
        desc = data.get("description", "")
        tool_id = data.get("toolId", "")

        if ntype == "end":
            lines.append(f"{step_num}. **[Ende]** {label or 'Workflow abgeschlossen — gib deine Antwort aus.'}")
        elif ntype == "source":
            src_type = data.get("sourceType", "")
            src_id = data.get("sourceId", "")
            lines.append(f"{step_num}. **[Quelle: {src_type or 'extern'}]** {label}")
            if src_id:
                lines.append(f"   → Ressource: `{src_id}`")
            if desc:
                lines.append(f"   → {desc}")
        elif ntype == "branch":
            condition = data.get("condition", label or "Bedingung prüfen")
            lines.append(f"{step_num}. **[Entscheidung]** {condition}")
            out_edges = edge_map.get(node["id"], [])
            for oe in out_edges:
                handle = oe.get("sourceHandle", "")
                target_node = node_map.get(oe["target"])
                target_label = target_node.get("data", {}).get("label", oe["target"]) if target_node else oe["target"]
                branch_label = "Ja" if handle == "true" else "Nein" if handle == "false" else handle or "→"
                lines.append(f"   → {branch_label}: weiter mit '{target_label}'")
        else:  # step
            tool_hint = f" (Tool: `{tool_id}`)" if tool_id else ""
            lines.append(f"{step_num}. **[Schritt]** {label}{tool_hint}")
            if desc:
                lines.append(f"   → {desc}")

        step_num += 1

    lines += [
        "",
        "Arbeite jeden Schritt der Reihe nach ab bevor du antwortest.",
    ]
    return "\n".join(lines)


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
    semantic_scores: dict[str, float] = {}
    if all_skills:
        skill_texts = [f"{s.skill} {' '.join(s.triggers)} {s.content[:300]}" for s in all_skills]
        # Nicht blockierend aufrufen wenn ein Event-Loop läuft (#94)
        try:
            asyncio.get_running_loop()
            # Async-Kontext: Semantic Scoring überspringen (score_texts würde Event-Loop blockieren)
            raw: list[float] = []
        except RuntimeError:
            raw = score_texts(skill_texts, user_text)
        if raw:
            semantic_scores = {s.skill: raw[i] for i, s in enumerate(all_skills)}
    active = select_skills(all_skills, user_text, semantic_scores=semantic_scores)
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


def _pre_compact_memory_flush(boss_cfg, session, compact_model: str) -> None:
    """Schreibt Kernfakten der Session ins Memory BEVOR kompaktiert wird.

    So gehen wichtige Informationen nicht verloren auch wenn die
    Compaction-Summary zu kurz ist oder der Agent /clear macht.
    """
    from .session_manager import MessageRole
    from datetime import datetime, timezone

    memory_dir = boss_cfg.agent_dir / "memory"
    memory_dir.mkdir(exist_ok=True)

    # Letzte User-Messages extrahieren (max 10)
    user_msgs = []
    for m in reversed(session.messages):
        if m.role == MessageRole.USER and m.content.strip():
            user_msgs.append(m.content[:200])
            if len(user_msgs) >= 10:
                break
    user_msgs.reverse()

    # Letzte Assistant-Messages (Kernentscheidungen)
    asst_msgs = []
    for m in reversed(session.messages):
        if m.role == MessageRole.ASSISTANT and m.content.strip() and len(m.content) > 50:
            asst_msgs.append(m.content[:300])
            if len(asst_msgs) >= 5:
                break
    asst_msgs.reverse()

    if not user_msgs:
        return

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    content = f"# Pre-Compact Snapshot ({now})\n\n"
    content += "## Letzte Aufgaben\n"
    for msg in user_msgs:
        content += f"- {msg}\n"
    content += "\n## Letzte Antworten (Zusammenfassung)\n"
    for msg in asst_msgs:
        first_line = msg.split("\n")[0]
        content += f"- {first_line}\n"

    # In Memory schreiben (überschreibt vorherigen Snapshot)
    snapshot_path = memory_dir / "_pre_compact_snapshot.md"
    snapshot_path.write_text(content, encoding="utf-8")
    snapshot_path.chmod(0o600)
    logger.info("Pre-compact memory snapshot geschrieben für %s", boss_cfg.id)


async def _compact_if_needed(
    sessions,
    project_id: str,
    boss_cfg,
    *,
    keep_last: int = 10,
) -> None:
    """
    Mehrstufige Context-Kompaktierung (#47, #349 OpenClaw-Qualität).

    Stufe 1 — Rolling Summary (strukturiert):
      Wenn estimated_tokens > token_threshold: die ältesten Nachrichten (alles
      außer den letzten keep_last) werden per LLM zusammengefasst.
      Format: Goal / Constraints / Progress (Done/InProgress/Blocked)

    Stufe 2 — Meta-Summary:
      Wenn nach Stufe 1 die Session immer noch > token_threshold ist,
      wird die Summary auf max 300 Wörter verdichtet.

    Threshold (#349): erhöht auf 15k estimated (~40k real, wie OpenClaw).
    keep_last: 10 Messages (vorher 6).
    """
    from .orchestrator_llm import _llm_with_retry
    from .session_manager import MessageRole

    # Agent-spezifischer Override oder Model-basierter Default
    if getattr(boss_cfg, "compaction_threshold", None):
        token_threshold = boss_cfg.compaction_threshold
    else:
        model = boss_cfg.llm.model.lower()
        if any(x in model for x in ("claude", "gpt-4", "gpt-3.5", "gemini", "mistral-large", "openai-codex", "gpt-5")):
            token_threshold = 40_000  # 40k estimated ≈ 100k real — Claude hat 200k, viel Spielraum
        else:
            token_threshold = 8_000

    # #416: Full-Compaction bei 80% des Context-Windows
    ctx_window = _context_window_for_model(boss_cfg.llm.model)
    full_compaction_threshold = int(ctx_window * 0.80)

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

    # Pre-Compact Memory Flush: Kontext ins Memory schreiben bevor er kompaktiert wird
    if boss_cfg.agent_dir:
        try:
            _pre_compact_memory_flush(boss_cfg, session, compact_model)
        except Exception as e:
            logger.debug("Pre-compact memory flush failed: %s", e)

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

    # #349: Strukturierte Summary im OpenClaw-Format
    _structured_format = (
        "Erstelle eine strukturierte Zusammenfassung in diesem Format:\n\n"
        "## Ziel\nWas ist das übergeordnete Ziel der Konversation?\n\n"
        "## Kontext & Entscheidungen\nWichtige Fakten, Constraints, getroffene Entscheidungen.\n\n"
        "## Fortschritt\n### Erledigt\n- [x] Was wurde abgeschlossen?\n\n"
        "### In Arbeit\n- [ ] Woran wird gerade gearbeitet?\n\n"
        "### Blockiert\n- **Problem**: Was blockiert und warum?\n\n"
        "Antworte NUR mit der Zusammenfassung, keine Einleitung oder Erklärung."
    )

    if existing_summary:
        user_content = (
            f"BISHERIGE ZUSAMMENFASSUNG:\n{existing_summary}\n\n"
            f"NEUE NACHRICHTEN:\n{history_lines}"
        )
        system_instruction = (
            "Du bekommst eine bisherige Zusammenfassung plus neue Nachrichten. "
            "Aktualisiere die Zusammenfassung — behalte alles Wichtige, "
            "verschiebe erledigte Punkte nach 'Erledigt'.\n\n"
            + _structured_format
        )
    else:
        user_content = history_lines
        system_instruction = (
            "Fasse die folgende Konversation zusammen.\n\n"
            + _structured_format
        )

    summary_prompt = [
        {"role": "system", "content": system_instruction},
        {"role": "user",   "content": user_content},
    ]

    try:
        resp = await _llm_with_retry(lambda: litellm.acompletion(
            model=compact_model,
            messages=summary_prompt,
            max_tokens=1200,
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

        # Stufe 3 (#416): Full-Compaction bei 80% Context-Window
        # Aggressiver: keep_last auf 4 reduzieren, alle Tool-Results entfernen
        current = sessions.estimated_tokens(project_id)
        if current >= full_compaction_threshold:
            logger.warning(
                "Full-Compaction triggered (Projekt: %s, ~%d Tokens >= 80%% von %d)",
                project_id, current, ctx_window,
            )
            # Tool-Messages aus den behaltenen Nachrichten entfernen
            session = sessions.get_active(project_id)
            if session:
                cleaned = [
                    m for m in session.messages
                    if m.role != MessageRole.TOOL
                ]
                session.messages = cleaned
                sessions._persist(session)
            # Nochmal kompaktieren mit weniger keep_last
            await sessions.compact(project_id, summary, keep_last=4)
            logger.info(
                "Full-Compaction abgeschlossen (Projekt: %s, ~%d Tokens)",
                project_id, sessions.estimated_tokens(project_id),
            )

        # Memory Flush: Summary in Memory-Datei schreiben
        # damit zukünftige Sessions relevante Fakten via BM25 finden
        if boss_cfg.agent_dir:
            _flush_summary_to_memory(boss_cfg.agent_dir, summary)

    except Exception as e:
        logger.warning("Context-Kompaktierung fehlgeschlagen: %s", e)
        current_tokens = sessions.estimated_tokens(project_id)
        if current_tokens > 80_000:  # Emergency Reset nur bei wirklichem Overflow
            logger.error(
                "Context-Notfall-Reset (Projekt: %s, ~%d geschätzte Tokens > 40k)",
                project_id, current_tokens,
            )
            await sessions.new_session(project_id)
