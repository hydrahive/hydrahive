"""
orchestrator_context.py — Context-Compaction & Memory-Budget

Standalone-Funktionen für System-Prompt-Aufbau und Context-Kompaktierung:
- _context_mode: normal vs. full anhand der User-Nachricht
- build_system_prompt: einziger autoritativer Builder (#636) — Soul + Memory +
  Skills + Channels strukturiert, (static, dynamic)-Tuple-Return
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
from .session_metrics import metrics as _metrics
from .context_channels import ContextChannels
from .memory_search import search_memory, update_index as update_memory_index
from .prefetch import start_memory_prefetch
from .semantic_index import score_texts
from .settings import settings
from .skill_loader import load_skills, select_skills, skills_to_system_prompt, Skill
from .skill_resolver import resolve_prompt_skills

logger = logging.getLogger(__name__)


# ── A-MEM Globaler Wissens-Prefetch (#563) ────────────────────────────────────

# A-MEM MCP-Server Config (SSE auf localhost)
_AMEM_SERVER_CFG: dict = {
    "url": "http://127.0.0.1:8020/sse",
    "transport": "sse",
    "headers": {},
}
_AMEM_ENABLED: bool | None = None  # None = noch nicht geprüft


async def _amem_check_available() -> bool:
    """Prüft einmalig ob A-MEM erreichbar ist. Cached das Ergebnis."""
    global _AMEM_ENABLED
    if _AMEM_ENABLED is not None:
        return _AMEM_ENABLED
    try:
        from .mcp_client import list_mcp_tools
        tools = await list_mcp_tools("amem", _AMEM_SERVER_CFG)
        _AMEM_ENABLED = any(t.get("name") == "amem_search" for t in tools)
        if _AMEM_ENABLED:
            logger.info("A-MEM verfügbar: %d Tools", len(tools))
        else:
            logger.info("A-MEM erreichbar aber amem_search nicht gefunden")
    except Exception as e:
        _AMEM_ENABLED = False
        logger.debug("A-MEM nicht verfügbar: %s", e)
    return _AMEM_ENABLED


async def _amem_global_search(query: str, k: int = 3, max_chars: int = 2000) -> str:
    """Sucht in A-MEM nach globalem Wissen. Gibt formatierten String zurück.

    Timeout: 5 Sekunden. Wenn A-MEM nicht erreichbar → leerer String.
    Ergebnis wird auf max_chars begrenzt.
    """
    if not query or len(query.strip()) < 3:
        return ""
    if not await _amem_check_available():
        return ""

    try:
        from .mcp_client import call_mcp_tool
        result = await asyncio.wait_for(
            call_mcp_tool("amem", _AMEM_SERVER_CFG, "amem_search", {"query": query, "k": k}),
            timeout=5.0,
        )
        if not result or not result.strip():
            return ""
        # Auf max_chars begrenzen
        if len(result) > max_chars:
            result = result[:max_chars] + "\n…[A-MEM Ergebnis gekürzt]"
        logger.debug("A-MEM Prefetch: %d Zeichen für '%s'", len(result), query[:50])
        return result
    except asyncio.TimeoutError:
        logger.debug("A-MEM Prefetch Timeout (5s) für '%s'", query[:50])
        return ""
    except Exception as e:
        logger.debug("A-MEM Prefetch Fehler: %s", e)
        return ""


def amem_invalidate() -> None:
    """A-MEM Verfügbarkeits-Cache zurücksetzen (z.B. nach Service-Restart)."""
    global _AMEM_ENABLED
    _AMEM_ENABLED = None


# Python-seitiger System-Prompt-Cache (#636: nur noch Static-Cache, der alte
# Voll-Prompt-Cache des entfernten Builders ist ersatzlos weggefallen).
# Format: agent_id → (static_str, timestamp, cache_hash)
_PROMPT_CACHE_TTL = 300  # 5 Min — gleich wie Anthropic ephemeral cache
_STATIC_PROMPT_CACHE: dict[str, tuple[str, float, str]] = {}


def _prompt_cache_key(agent_id: str, agent_dir=None) -> str:
    """Namespaced Cache-Key: trennt Personal-Agent und Personal-Projekt-Boss,
    die beide `agent_id="personal_<user>"` tragen, aber unterschiedliche
    agent_dir haben. Ohne agent_dir bleibt der flache Key erhalten."""
    if agent_dir is None:
        return agent_id
    try:
        return f"{agent_id}:{Path(agent_dir).resolve()}"
    except (OSError, ValueError):
        return f"{agent_id}:{agent_dir}"


def invalidate_prompt_cache(agent_id: str) -> None:
    """Löscht den gecachten Static-Prompt-Anteil eines Agenten.

    Aufrufer kennen typischerweise nur die `agent_id`, nicht den `agent_dir`.
    Um die gleiche Semantik wie vor dem Key-Namespacing zu erhalten, löschen
    wir sowohl den flachen Legacy-Key als auch alle namespaced Varianten.
    """
    _STATIC_PROMPT_CACHE.pop(agent_id, None)
    prefix = f"{agent_id}:"
    for key in [k for k in _STATIC_PROMPT_CACHE if k.startswith(prefix)]:
        _STATIC_PROMPT_CACHE.pop(key, None)

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


# #527: Cache-Segment-Hashes für Break-Detection
_SEGMENT_HASHES: dict[str, dict[str, str]] = {}  # agent_id → {segment_name: hash}


_CORE_POLICY_PATH: Path = Path(__file__).parent / "prompts" / "agent_default_policy.md"
_CORE_POLICY_WARN_CHARS = 6000  # Warnung ab dieser Größe (entspricht ~1500 Tokens)


def _load_core_policy_text() -> str:
    """#710: Lädt die repo-weite Core-Policy aus der versionierten Markdown-Datei.

    Gibt leeren String zurück, wenn die Datei fehlt oder nicht lesbar ist —
    der Prompt-Build bricht in keinem Fall. Ungewöhnlich große Policies
    (> _CORE_POLICY_WARN_CHARS) erzeugen einen Warn-Log, werden aber nicht
    gekürzt. Der Pfad ist via Modul-Attribut (`_CORE_POLICY_PATH`) monkey-
    patchbar — Tests können ihn gezielt umleiten.
    """
    try:
        path = _CORE_POLICY_PATH
        if not path.exists():
            logger.debug("core-policy: Datei %s nicht vorhanden", path)
            return ""
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning("core-policy: konnte %s nicht lesen: %s", _CORE_POLICY_PATH, exc)
        return ""
    if text and len(text) > _CORE_POLICY_WARN_CHARS:
        logger.warning(
            "core-policy: %s ist %d Zeichen (> %d) — Budget prüfen",
            _CORE_POLICY_PATH.name, len(text), _CORE_POLICY_WARN_CHARS,
        )
    return text


def _prompt_cache_hash(agent_dir: Path, mode: str) -> str:
    """Hash über alle Faktoren die den System-Prompt beeinflussen."""
    parts = [mode]
    # #710: Core-Policy-Datei muss in den Hash — Änderung soll Static-Cache
    # invalidieren, sonst bleiben Policy-Edits bis TTL-Ablauf unsichtbar.
    if _CORE_POLICY_PATH.exists():
        parts.append(f"core_policy:{_CORE_POLICY_PATH.stat().st_mtime:.0f}")
    handbook = settings.system_handbook
    if handbook.exists():
        parts.append(f"handbook:{handbook.stat().st_mtime:.0f}")
    soul = agent_dir / "soul.md"
    if soul.exists():
        parts.append(f"soul:{soul.stat().st_mtime:.0f}")
    # v2: AGENT.md ist die identitätskritische Persona-Quelle. Vorher fehlte
    # sie im Hash → Edits an AGENT.md blieben bis TTL-Ablauf unsichtbar.
    agent_md = agent_dir / "AGENT.md"
    if agent_md.exists():
        parts.append(f"agent_md:{agent_md.stat().st_mtime:.0f}")
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


def _diagnose_cache_break(agent_id: str, agent_dir: Path, mode: str) -> str | None:
    """#527: Identifiziert welches Segment den Cache-Break verursacht hat."""
    segments: dict[str, str] = {}
    segments["mode"] = mode
    if _CORE_POLICY_PATH.exists():
        segments["core_policy"] = f"{_CORE_POLICY_PATH.stat().st_mtime:.0f}"
    handbook = settings.system_handbook
    if handbook.exists():
        segments["handbook"] = f"{handbook.stat().st_mtime:.0f}"
    soul = agent_dir / "soul.md"
    if soul.exists():
        segments["soul"] = f"{soul.stat().st_mtime:.0f}"
    agent_md = agent_dir / "AGENT.md"
    if agent_md.exists():
        segments["agent_md"] = f"{agent_md.stat().st_mtime:.0f}"
    memory_dir = agent_dir / "memory"
    if memory_dir.exists():
        mem_mtimes = [f"{f.name}:{f.stat().st_mtime:.0f}" for f in sorted(memory_dir.glob("*.md"))]
        segments["memory"] = hashlib.sha256("|".join(mem_mtimes).encode()).hexdigest()[:8]
    skills_dir = agent_dir / "skills"
    if skills_dir.exists():
        skill_mtimes = [f"{f.name}:{f.stat().st_mtime:.0f}" for f in sorted(skills_dir.glob("*.md"))]
        segments["skills"] = hashlib.sha256("|".join(skill_mtimes).encode()).hexdigest()[:8]
    wf_flow = agent_dir / "workflow_flow.json"
    if wf_flow.exists():
        segments["workflow"] = f"{wf_flow.stat().st_mtime:.0f}"

    old = _SEGMENT_HASHES.get(agent_id, {})
    _SEGMENT_HASHES[agent_id] = segments

    if not old:
        return None  # Erster Aufruf — kein Vergleich möglich

    changed = []
    for key, val in segments.items():
        if old.get(key) != val:
            changed.append(key)
    for key in old:
        if key not in segments:
            changed.append(f"-{key}")

    if changed:
        return f"cache-break: {', '.join(changed)}"
    return None


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


def _resolve_user_skills_dir(request_user: str | None) -> "Path | None":
    """#668: Ermittelt den User-Skill-Ordner für den aktuellen Request.

    Liefert None, wenn kein echter authentifizierter User vorliegt
    (`None` oder Internal-Marker `"internal"`) — dann bleibt der User-Layer
    im Prompt inaktiv. Ungültige Usernames werden geloggt und übersprungen,
    statt den Prompt-Build zu brechen.
    """
    if request_user is None or request_user == "internal":
        return None
    try:
        return settings.user_skills_dir(request_user)
    except ValueError:
        logger.warning(
            "skill-resolver: ungültiger request_user %r — User-Layer übersprungen",
            request_user,
        )
        return None


async def build_system_prompt(
    boss_cfg, user_text: str, *,
    invalidate: bool = False, session=None,
    request_user: str | None = None,
) -> tuple[str, str]:
    """
    #636: Einziger autoritativer Builder. Wird von non-stream, OAuth-stream
    und litellm-stream gleichermaßen aufgerufen — kein Parallel-Builder,
    kein Fallback. Aufrufer joinen Tuple mit:
    `(static + "\\n\\n" + dynamic).strip() if dynamic else static`.

    Baut den System-Prompt strukturiert in ContextChannels (#627) und gibt
    (static_prefix, dynamic_suffix) zurück.

    Static-Channels (cacheable) und Dynamic-Channels (query-abhängig) werden
    pro Slot zugeordnet, nicht in eine flache Parts-Liste gekippt. Der
    dynamische Memory-Block wird im Output mit `<memory_dynamic>` markiert,
    damit #629 (Cache-Segmentierung) und Debugging die Grenze erkennen.

    `session` ist optional und wird nur für den `working_state`-Channel (#632)
    gebraucht — wenn vorhanden, wird `session.working_state.to_channel_text()`
    in den Dynamic-Block injiziert (zwischen Memory und Skills, vor dem Marker).
    """
    mode = _context_mode(user_text)
    loop = asyncio.get_event_loop()

    # #625: Memory-Prefetch frühestmöglich starten — läuft parallel zum static-Build
    _k_prefetch = 8 if mode == "full" else 4
    _prefetch = start_memory_prefetch(boss_cfg.agent_dir, user_text, k=_k_prefetch)

    channels = ContextChannels()
    # Identity + Datum/Uhrzeit (Datum war im alten Builder als zweiter Slot,
    # fehlt im Split-Builder → vom Review als Bug markiert)
    from datetime import datetime, timezone as _tz
    _now = datetime.now(_tz.utc)
    # v2 hat AGENT.md → Identitätsstatement weglassen damit es nicht doppelt
    # mit der AGENT.md konkurriert (siehe alter Builder Z. 270-279)
    _has_agent_md = bool(
        boss_cfg.agent_dir and (boss_cfg.agent_dir / "AGENT.md").exists()
    )
    _identity_lines = []
    if not _has_agent_md:
        _identity_lines.append(f"Du bist {boss_cfg.identity}.")
    _identity_lines.append(
        f"Aktuelles Datum: {_now.strftime('%A, %d. %B %Y')}. "
        f"Uhrzeit: {_now.strftime('%H:%M')} UTC."
    )
    channels.agent_identity = "\n".join(_identity_lines)

    # ── Static-Cache prüfen (cached den serialisierten static-Block) ──────
    static_cached = None
    _cache_key = _prompt_cache_key(boss_cfg.id, boss_cfg.agent_dir) if boss_cfg.agent_dir else boss_cfg.id
    if not invalidate and boss_cfg.agent_dir:
        cached = _STATIC_PROMPT_CACHE.get(_cache_key)
        if cached:
            prompt_s, ts, h = cached
            if (time.time() - ts) < _PROMPT_CACHE_TTL:
                current_h = _prompt_cache_hash(boss_cfg.agent_dir, mode)
                if current_h == h:
                    static_cached = prompt_s

    if static_cached is None:
        # ── Static-Channels füllen ────────────────────────────────────
        if boss_cfg.agent_dir:
            startup_path = boss_cfg.agent_dir / "startup.md"
            if startup_path.exists():
                startup_text = startup_path.read_text(encoding="utf-8").strip()
                if startup_text:
                    channels.onboarding = (
                        "## ERSTER START — ONBOARDING\n\n"
                        "**WICHTIG: Diese Anweisung hat höchste Priorität.**\n\n"
                        f"{startup_text}"
                    )

        # Identitätskritischer Body: v2 → AGENT.md, v1 → soul.md aus agent.yaml.
        # Review-Bug: vorher wurde nur boss_cfg.soul gelesen, was bei v2 None
        # ist → Streaming-Pfad lief ohne AGENT.md-Inhalt.
        if boss_cfg.agent_dir:
            agent_md_path = boss_cfg.agent_dir / "AGENT.md"
            if agent_md_path.exists():
                channels.soul = agent_md_path.read_text(encoding="utf-8").strip()
            elif boss_cfg.soul:
                soul_path = boss_cfg.agent_dir / boss_cfg.soul
                if soul_path.exists():
                    channels.soul = soul_path.read_text(encoding="utf-8").strip()

        if boss_cfg.agent_dir:
            memory_dir = boss_cfg.agent_dir / "memory"
            if memory_dir.exists():
                from .memory_paths import MEMORY_INDEX_FILENAME, LEGACY_MEMORY_INDEX_FILENAME
                index_path = memory_dir / MEMORY_INDEX_FILENAME
                if not index_path.exists():
                    index_path = memory_dir / LEGACY_MEMORY_INDEX_FILENAME
                if index_path.exists():
                    index_text = index_path.read_text(encoding="utf-8").strip()
                    if index_text:
                        if len(index_text) > 1500:
                            index_text = index_text[:1500] + f"\n…[{index_path.name} gekürzt]"
                        channels.memory_index = f"## Persistentes Gedächtnis\n\n### Index\n{index_text}"
                # #636: konsistentes Memory-Budget statt hardcoded
                from .context_lifecycle import get_memory_budget as _gmb
                _mem_budget = _gmb(mode)
                learning_snippet = build_learning_prompt_snippet(
                    boss_cfg.agent_dir,
                    **({"max_entries": 8, "max_chars": _mem_budget} if mode == "full"
                       else {"max_entries": 3, "max_chars": min(_mem_budget, 1500)}),
                )
                if learning_snippet:
                    channels.learning = learning_snippet

        if getattr(boss_cfg, "sources", None):
            src_lines = [f"- **{s.name}**: {s.url}" + (f" — {s.description}" if s.description else "") for s in boss_cfg.sources]
            channels.sources = "## Zugewiesene Quellen\n\n" + "\n".join(src_lines)

        try:
            from .repo_config import repos_for_agent
            agent_repos = repos_for_agent(boss_cfg.id)
            if agent_repos:
                repo_lines = []
                for repo in agent_repos:
                    clone_url = repo.url
                    if repo.token and "github.com" in repo.url:
                        clone_url = repo.url.replace("https://", f"https://{repo.token}@")
                    repo_lines.append(f"- **{repo.name}** ({repo.provider}): Clone `{clone_url}.git` Branch `{repo.branch}`")
                channels.repos = "## Zugewiesene Git-Repos\n\n" + "\n".join(repo_lines)
        except Exception:
            pass

        # #584-A: Projekt-Target-Injektion mit Legacy-Fallback.
        # Bei v2-Projekt-Agents (Bridge agent_config_from_project) ist boss_cfg.id == project_id
        # und boss_cfg.project_dir ist gesetzt. Dann Projekt-Targets bevorzugen;
        # Legacy agent_servers-Block fällt weg, wenn Targets existieren — keine
        # doppelte/widersprüchliche Liste.
        _targets_injected = False
        if getattr(boss_cfg, "project_dir", None) is not None:
            try:
                from .project_targets import render_project_targets_for_prompt
                _targets_block = render_project_targets_for_prompt(boss_cfg.id)
                if _targets_block:
                    channels.servers = _targets_block
                    _targets_injected = True
            except Exception as _t_err:
                logger.debug("Project-Target-Injection übersprungen: %s", _t_err)

        # #636: Legacy Remote-Server-Injektion (agent_servers.json → agent-basiert).
        # Nur aktiv, wenn #584-A-Targets leer/unset sind, damit der Prompt nicht
        # zwei widersprüchliche Server-Listen zeigt.
        if not _targets_injected:
            try:
                from .router_servers import _load_agent_servers, _load_servers
                agent_servers_map = _load_agent_servers()
                assigned_ids = agent_servers_map.get(boss_cfg.id, [])
                if assigned_ids:
                    all_servers = {s["id"]: s for s in _load_servers()}
                    srv_lines = []
                    for sid in assigned_ids:
                        srv = all_servers.get(sid)
                        if srv:
                            srv_lines.append(
                                f"- **{srv.get('name', sid)}** (ID: `{sid}`): "
                                f"`{srv.get('ssh_user', '?')}@{srv.get('ip', '?')}:{srv.get('ssh_port', 22)}`"
                                + (f" — {srv.get('description', '')}" if srv.get("description") else "")
                            )
                    if srv_lines:
                        channels.servers = (
                            "## Zugewiesene Remote-Server\n\n"
                            "Diese Server sind dir zugewiesen. Nutze `server_shell` (mit `server_id`) "
                            "um Befehle auszuführen, und `server_file_read`/`server_file_write` für Dateien. "
                            "SSH-Keys werden automatisch geladen — NICHT manuell per ssh/shell_exec verbinden!\n\n"
                            + "\n".join(srv_lines)
                        )
            except Exception as _srv_err:
                logger.debug("Server-Injection übersprungen: %s", _srv_err)

        # #710: Core-Policy als versionierte Markdown-Datei statt Hardcode.
        # Agent-unabhängig — greift auch wenn kein agent_dir gesetzt ist.
        _core_policy_text = _load_core_policy_text()
        if _core_policy_text:
            channels.policies = _core_policy_text

        _handbook_path = settings.system_handbook
        if _handbook_path.exists():
            _handbook_text = _handbook_path.read_text(encoding="utf-8").strip()
            if _handbook_text:
                channels.handbook = _handbook_text

        if boss_cfg.agent_dir:
            blueprint_ctx = _load_agent_blueprint_context(boss_cfg.agent_dir)
            if blueprint_ctx:
                channels.blueprint = blueprint_ctx
            agent_wf = _load_agent_workflow_prompt(boss_cfg.agent_dir)
            if agent_wf:
                channels.workflow = agent_wf

        static_cached = channels.to_static_str()
        if boss_cfg.agent_dir:
            h = _prompt_cache_hash(boss_cfg.agent_dir, mode)
            _STATIC_PROMPT_CACHE[_cache_key] = (static_cached, time.time(), h)

    # ── Dynamic-Channels füllen ───────────────────────────────────────
    _llm_cfg = getattr(boss_cfg, "llm", None)
    _runtime_model = str(getattr(_llm_cfg, "model", "") or "").strip()
    if _runtime_model:
        _provider = str(getattr(_llm_cfg, "provider", "") or "").strip()
        _provider_line = f"- Provider: `{_provider}`\n" if _provider else ""
        channels.runtime = (
            "## Runtime-LLM\n\n"
            "Dieses Projekt verwendet fuer diesen Turn folgendes LLM. "
            "Falls Memory, Session-Summaries oder fruehere Kontextnotizen ein anderes Modell nennen, "
            "sind diese Angaben veraltet und duerfen diese Runtime-Angabe nicht ueberschreiben.\n\n"
            f"{_provider_line}- Modell: `{_runtime_model}`"
        )

    if boss_cfg.agent_dir:
        # #659/#668: Multi-Layer-Resolver (agent > project > user).
        # System/Catalog ist bewusst KEIN automatischer Prompt-Layer —
        # Admin kontrolliert via `/skill install`, was prompt-wirksam wird.
        # User-Layer (#668): wird nur genutzt, wenn `request_user` ein
        # echter auth-User ist; `None`/`"internal"`/invalid → übersprungen.
        # Skill-Files liegen unter `<agent_dir>/skills/` (wie in `load_skills`);
        # der Resolver erwartet das konkrete Skills-Verzeichnis.
        _user_skills_dir = _resolve_user_skills_dir(request_user)
        _proj_dir = getattr(boss_cfg, "project_dir", None)
        _resolved_prompt, _resolver_errors = resolve_prompt_skills(
            agent_dir=boss_cfg.agent_dir / "skills",
            project_dir=(_proj_dir / "skills") if _proj_dir else None,
            user_skills_dir=_user_skills_dir,
        )
        if _resolver_errors:
            logger.debug("skill-resolver: %d Fehler ignoriert: %s",
                         len(_resolver_errors), _resolver_errors[:3])
        all_skills: list[Skill] = [
            r.effective.skill for r in _resolved_prompt
            if r.effective.parsed_ok and r.effective.skill is not None
        ]
        _skill_task = None
        if all_skills:
            skill_texts = [f"{s.skill} {' '.join(s.triggers)} {s.content[:300]}" for s in all_skills]
            _skill_task = loop.run_in_executor(None, score_texts, skill_texts, user_text)

        # #625: Async Memory-Prefetch — BM25-Treffer abholen
        snippets = await _prefetch.get_bm25(timeout=0.8)
        if snippets:
            channels.memory_hits = "### Erinnerungen (query-relevant)\n" + "\n---\n".join(snippets)

        # #636: A-MEM globaler Wissens-Prefetch (aus altem Builder migriert).
        amem_snippets = await _prefetch.get_amem(timeout=1.5)
        if amem_snippets:
            channels.amem_hits = "## Globales Wissen (A-MEM)\n\n" + amem_snippets

        if all_skills and _skill_task:
            raw_scores = await _skill_task
            semantic_scores = {s.skill: raw_scores[i] for i, s in enumerate(all_skills)} if raw_scores else {}
            active_skills = select_skills(all_skills, user_text, semantic_scores=semantic_scores)
            if active_skills:
                channels.skills = skills_to_system_prompt(active_skills, token_budget=8000)

    repo_guidance = _repo_review_guidance(boss_cfg, user_text)
    if repo_guidance:
        channels.repo_guidance = repo_guidance

    if boss_cfg.agent_dir:
        handoff_path = boss_cfg.agent_dir / "memory" / "_last_handoff.md"
        if handoff_path.exists():
            import os
            age_hours = (time.time() - os.path.getmtime(handoff_path)) / 3600
            if age_hours < 48:
                handoff_text = handoff_path.read_text(encoding="utf-8").strip()
                if handoff_text:
                    if len(handoff_text) > 3000:
                        handoff_text = handoff_text[:3000] + "\n…[gekürzt]"
                    channels.forced_handoff = (
                        "## Forced-Abort-Handoff\n\n"
                        "Dieser automatisch gespeicherte Stand stammt aus einem abgebrochenen Tool-Loop. "
                        "Verifiziere Pfade, Repo und TODOs vor weiteren Änderungen.\n\n"
                        + handoff_text
                    )

        last_session_path = boss_cfg.agent_dir / "memory" / "_last_session.md"
        if last_session_path.exists():
            import os
            age_hours = (time.time() - os.path.getmtime(last_session_path)) / 3600
            if age_hours < 24:
                last_text = last_session_path.read_text(encoding="utf-8").strip()
                if last_text:
                    if len(last_text) > 3000:
                        last_text = last_text[:3000] + "\n…[gekürzt]"
                    channels.last_session = "## Letzte Session\n\n" + last_text

    # #620: Deferred-Tools-Liste (lokal + MCP, Phase 4)
    try:
        from .tool_registry import render_deferred_tools_block, set_current_mcp_entries
        from .orchestrator_mcp import _mcp_deferred_entries
        _mcp_entries: list[tuple[str, str]] = []
        try:
            from .settings import settings as _s
            _mcp_entries = await _mcp_deferred_entries(boss_cfg, str(_s.mcp_servers_config))
        except Exception as _mcp_err:
            logger.debug("mcp deferred entries skipped: %s", _mcp_err)
        set_current_mcp_entries(boss_cfg.id, _mcp_entries)
        _deferred_block = render_deferred_tools_block(mcp_entries=_mcp_entries)
        if _deferred_block:
            channels.deferred_tools = _deferred_block
    except Exception as _e:
        logger.debug("deferred-tools block skipped: %s", _e)

    # #632: Working-State-Snapshot in den dynamischen Channel injizieren —
    # zeigt dem Agent was im letzten Turn lief, welche Files offen waren,
    # ob ein Git-Workspace im halbfertigen Zustand steckt etc.
    if session is not None and getattr(session, "working_state", None):
        try:
            ws_text = session.working_state.to_channel_text()
            if ws_text:
                channels.working_state = ws_text
        except Exception as _ws_err:
            logger.debug("working_state Channel-Render fehlgeschlagen: %s", _ws_err)

    dynamic_suffix = channels.to_dynamic_str()

    # #627: Channel-Sizes loggen (Diagnose)
    _filled = {k: v for k, v in channels.sizes().items() if v > 0}
    logger.debug("system-prompt channels (agent=%s): %s", boss_cfg.id, _filled)

    return static_cached, dynamic_suffix


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

        # #514: Learning after Compact — Fakten aus Summary extrahieren
        _extract_learnings(memory_dir, summary)

        # Wiki-Integration: Summary ins BookStack Wiki schreiben (wenn konfiguriert)
        try:
            _wiki_config_path = Path("/etc/hydrahive/bookstack.json")
            if _wiki_config_path.exists():
                import httpx
                _wiki_cfg = json.loads(_wiki_config_path.read_text())
                _wiki_url = _wiki_cfg.get("base_url", "").rstrip("/")
                _wiki_tid = _wiki_cfg.get("token_id", "")
                _wiki_ts = _wiki_cfg.get("token_secret", "")
                if _wiki_url and _wiki_tid and _wiki_ts:
                    _wiki_headers = {
                        "Authorization": f"Token {_wiki_tid}:{_wiki_ts}",
                        "Content-Type": "application/json",
                    }
                    # "Lessons Learned" Buch finden
                    _wb = httpx.get(f"{_wiki_url}/api/books", params={"count": 50},
                                     headers=_wiki_headers, timeout=5)
                    _book_id = None
                    for _b in _wb.json().get("data", []):
                        if "lesson" in _b.get("name", "").lower():
                            _book_id = _b["id"]
                            break
                    if _book_id:
                        import datetime as _dt
                        _now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
                        httpx.post(f"{_wiki_url}/api/pages",
                                    json={"name": f"Session Summary {_now}",
                                          "markdown": summary[:5000], "book_id": _book_id,
                                          "tags": [{"name": "auto-compact"}, {"name": "session-summary"}]},
                                    headers=_wiki_headers, timeout=10)
                        logger.debug("Wiki: Session Summary ins BookStack geschrieben")
        except Exception as _wiki_err:
            logger.debug("Wiki auto-write skipped: %s", _wiki_err)
    except OSError as e:
        logger.warning("Memory Flush fehlgeschlagen: %s", e)


def _extract_learnings(memory_dir: Path, summary: str) -> None:
    """#514: Extrahiert wiederverwendbare Fakten aus einer Compaction-Summary.

    Sucht nach konkreten Entscheidungen, technischen Details und Ergebnissen
    und schreibt sie in eine separate Learning-Datei.
    """
    import re
    learnings: list[str] = []

    # Erledigt-Punkte extrahieren (aus strukturierter Summary)
    done_pattern = re.compile(r'- \[x\]\s+(.+)', re.IGNORECASE)
    for match in done_pattern.finditer(summary):
        learnings.append(f"Erledigt: {match.group(1).strip()}")

    # Entscheidungen extrahieren
    decision_markers = ("entschieden", "gewählt", "festgelegt", "beschlossen", "decided", "chose")
    for line in summary.split("\n"):
        line = line.strip()
        if any(m in line.lower() for m in decision_markers) and len(line) > 20:
            learnings.append(f"Entscheidung: {line[:200]}")

    # Blockiert/Problem-Punkte
    blocked_pattern = re.compile(r'(?:blockiert|problem|fehler|bug):\s*(.+)', re.IGNORECASE)
    for match in blocked_pattern.finditer(summary):
        learnings.append(f"Problem: {match.group(1).strip()[:200]}")

    if not learnings:
        return

    import datetime
    learning_file = memory_dir / "_learnings.md"
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"\n### {timestamp}\n" + "\n".join(f"- {l}" for l in learnings[:10]) + "\n"

    try:
        # Append, max 50 Einträge behalten
        existing = ""
        if learning_file.exists():
            existing = learning_file.read_text(encoding="utf-8")
        combined = existing + entry
        # Trim: nur letzte 50 Sections behalten
        sections = combined.split("\n### ")
        if len(sections) > 50:
            combined = "\n### ".join(sections[-50:])
        learning_file.write_text(combined, encoding="utf-8")
        logger.debug("Learnings extrahiert: %d Fakten → %s", len(learnings), learning_file.name)
    except OSError as e:
        logger.debug("Learnings-Extraktion fehlgeschlagen: %s", e)


def get_skill_tool_constraints(
    boss_cfg, user_text: str, *,
    request_user: str | None = None,
) -> tuple[list[str], list[str]]:
    """
    Gibt (allowed_tools, blocked_tools) der aktiven Skills zurück.
    Kombinationsregel über mehrere aktive Skills:
    - allowed_tools: Vereinigung (jeder Skill kann Tools freischalten)
    - blocked_tools: Vereinigung (jeder Skill kann Tools sperren)
    - blocked_tools gewinnt bei Konflikt
    Wenn kein aktiver Skill Tool-Constraints hat → leere Listen (keine Einschränkung).

    #668: `request_user` aktiviert den User-Layer identisch zu
    `build_system_prompt`. None/internal/invalid → User-Layer übersprungen.
    """
    if not boss_cfg.agent_dir:
        return [], []
    # #659/#668: gleicher Resolver wie build_system_prompt — konsistente Layer-Sicht.
    _proj_dir = getattr(boss_cfg, "project_dir", None)
    _resolved_prompt, _ = resolve_prompt_skills(
        agent_dir=boss_cfg.agent_dir / "skills",
        project_dir=(_proj_dir / "skills") if _proj_dir else None,
        user_skills_dir=_resolve_user_skills_dir(request_user),
    )
    all_skills: list[Skill] = [
        r.effective.skill for r in _resolved_prompt
        if r.effective.parsed_ok and r.effective.skill is not None
    ]
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


def _extract_edited_files(messages, *, max_files: int = 5) -> list[str]:
    """#467: Extrahiere Pfade der zuletzt bearbeiteten Files aus Tool-Messages."""
    import re
    from .session_manager import MessageRole
    paths: list[str] = []
    seen: set[str] = set()
    # Rückwärts durchgehen → neueste zuerst
    for m in reversed(messages):
        if m.role != MessageRole.TOOL:
            continue
        content = m.content
        # file_write|/path/to/file oder file_edit|/path/to/file
        if content.startswith(("file_write|", "file_edit|")):
            # Format: "tool_name|detail text"
            detail = content.split("|", 1)[1] if "|" in content else ""
            # Pfad ist typischerweise das erste Wort oder in der Detail-Zeile
            match = re.search(r"(/[\w./_-]+)", detail)
            if match and match.group(1) not in seen:
                seen.add(match.group(1))
                paths.append(match.group(1))
        if len(paths) >= max_files:
            break
    return paths


def _build_reinject_context(file_paths: list[str], *, max_chars_per_file: int = 2000, total_budget: int = 8000) -> str:
    """#467: Liest die zuletzt bearbeiteten Files und baut einen Reinject-Block."""
    snippets: list[str] = []
    total = 0
    for fp in file_paths:
        p = Path(fp)
        if not p.is_file():
            continue
        try:
            content = p.read_text(errors="replace")[:max_chars_per_file]
            entry = f"### {fp}\n```\n{content}\n```"
            if total + len(entry) > total_budget:
                break
            snippets.append(entry)
            total += len(entry)
        except Exception:
            continue
    if not snippets:
        return ""
    return (
        "[Kontext-Reinjektion nach Kompaktierung — zuletzt bearbeitete Dateien]\n\n"
        + "\n\n".join(snippets)
    )


async def _compact_call(
    boss_cfg, compact_model: str, messages: list[dict], max_tokens: int,
) -> str:
    """Module-Level LLM-Call für Compaction, mit OAuth-Fallback.

    #628-Followup: Auf Kundenmaschinen ohne ANTHROPIC_API_KEY (nur OAuth
    konfiguriert) würde litellm sofort fehlschlagen → Compaction crasht →
    Notfall-Reset. Deshalb Claude-OAuth-Pfad zuerst, litellm als Fallback.

    Module-Level statt inner function, damit Tests den Call mocken können
    ohne Closure-Tricks (#660).
    """
    import os as _os
    from .orchestrator_llm import _llm_with_retry, _provider_call_kwargs, _resolve_model

    is_claude = compact_model.startswith(("claude-", "anthropic/"))
    has_api_key = bool(_os.environ.get("ANTHROPIC_API_KEY", "").strip())
    if is_claude and not has_api_key:
        from .orchestrator_llm import _anthropic_oauth_call, _load_claude_oauth_token
        token = (
            _os.environ.get("ANTHROPIC_API_KEY", "").strip()
            or _load_claude_oauth_token()
            or ""
        )
        if token:
            resp = await _anthropic_oauth_call(
                boss_cfg, messages, None, token, compact_model,
            )
            return getattr(resp.choices[0].message, "content", "") or ""
    model, api_base = _resolve_model(compact_model, getattr(boss_cfg.llm, "ollama_base_url", None))
    kwargs = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "drop_params": True,
    }
    if api_base:
        kwargs["api_base"] = api_base
    # #616: Compaction must use the same provider-specific transport kwargs as
    # normal LLM calls, otherwise bare MiniMax model IDs are sent to LiteLLM
    # without provider/base_url information.
    kwargs.update(_provider_call_kwargs(compact_model, boss_cfg))
    resp = await _llm_with_retry(lambda: litellm.acompletion(**kwargs))
    return resp.choices[0].message.content or ""


async def _compact_if_needed(
    sessions,
    project_id: str,
    boss_cfg,
    *,
    keep_last: int = 10,
    keep_last_rounds: int = 3,
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

    # #477: Round-basiert splitten — immer an Round-Grenzen schneiden
    from .session_manager import group_messages_by_api_round
    rounds = group_messages_by_api_round(msgs)
    if len(rounds) > keep_last_rounds:
        kept_rounds = rounds[-keep_last_rounds:]
        to_summarize_rounds = rounds[:-keep_last_rounds]
        to_summarize = [m for rnd in to_summarize_rounds for m in rnd]
    else:
        # Nicht genug Rounds → flat Fallback
        to_summarize = msgs[:-keep_last] if len(msgs) > keep_last else msgs[:]
    if not to_summarize and not existing_summary:
        return

    # #467: File-Pfade VOR Compaction extrahieren (Messages werden gleich entfernt)
    _edited_files = _extract_edited_files(session.messages)

    # #660: Tool-Interaktionen explizit miterfassen, damit die Summary weiß,
    # welche Tools aufgerufen wurden und was sie zurückgegeben haben.
    _history_parts = []
    for m in to_summarize:
        role = m.role.value.upper()
        content = m.content[:1500] if m.content else ""
        if m.role == MessageRole.TOOL:
            # TOOL Messages enthalten "tool_name|output" — klar kennzeichnen
            _history_parts.append(f"TOOL-RESULT: {content}")
        elif m.role == MessageRole.SYSTEM and content.startswith("🔧"):
            # System-Messages mit Tool-Marker = Tool-Call-Info
            _history_parts.append(f"TOOL-CALL: {content}")
        else:
            _history_parts.append(f"{role}: {content}")
    history_lines = "\n".join(_history_parts)

    # #660: Strukturierter Task-State + Output-Schema-Prompt im neuen Modul.
    # Fakten (working_state + deterministische Regex-Extraktion) werden dem
    # LLM wörtlich vorgelegt. History und Existing-Summary werden dabei
    # redacted (Secret-Patterns aus #657).
    from .compaction_summary import (
        build_summary_prompt as _build_summary_prompt,
        collect_task_state_facts as _collect_facts,
        redact_summary_text as _redact_summary,
    )
    _active = sessions.get_active(project_id)
    _facts = _collect_facts(_active, boss_cfg) if _active else None
    # Wenn keine Session da ist, entfällt der gesamte Compaction-Pfad weiter
    # unten ohnehin; defensiver Fallback für Typ-Check:
    from .compaction_summary import TaskStateFacts as _TSF
    if _facts is None:
        _facts = _TSF()
    summary_prompt = _build_summary_prompt(
        history_lines, _facts,
        existing_summary=existing_summary,
    )

    try:
        summary = await _compact_call(boss_cfg, compact_model, summary_prompt, 1200)
        if not summary:
            return
        # Redaction der LLM-Antwort vor Persist — das Modell könnte
        # ein Secret aus der History in der Summary wiederholen.
        summary = _redact_summary(summary)

        await sessions.compact(project_id, summary, keep_last=keep_last, keep_last_rounds=keep_last_rounds)
        _metrics.record_compaction(project_id, stage=1)
        # #523: Turn Journal — Compaction Event
        try:
            from .turn_journal import journal as _tj, EventType as _JE
            _sid = getattr(sessions.get_active(project_id), "id", "") if sessions.get_active(project_id) else ""
            _tj.append(_sid, project_id, _JE.COMPACTION, {"stage": 1, "tokens_after": sessions.estimated_tokens(project_id)})
        except Exception:
            pass
        logger.info(
            "Context kompaktiert Stufe-1 (Projekt: %s, ~%d Tokens nach Kompaktierung, %d Rounds behalten)",
            project_id, sessions.estimated_tokens(project_id), keep_last_rounds,
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
            meta = await _compact_call(boss_cfg, compact_model, meta_prompt, 350)
            if meta:
                # #660: Meta-Summary ebenfalls redacten vor Persist.
                meta = _redact_summary(meta)
                await sessions.compact(project_id, meta, keep_last=keep_last, keep_last_rounds=keep_last_rounds)
                summary = meta
                _metrics.record_compaction(project_id, stage=2)
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
            # Tool-Results auf Kurzform kürzen statt komplett entfernen
            session = sessions.get_active(project_id)
            if session:
                for m in session.messages:
                    if m.role == MessageRole.TOOL and len(m.content) > 200:
                        # Behalte Tool-Name + erste Zeile + Längeninfo
                        _tool_name = m.metadata.get("tool_name", "")
                        _first_line = m.content.split("\n", 1)[0][:120]
                        m.content = f"[{_tool_name}] {_first_line}… [{len(m.content)} Zeichen]"
                sessions._db_replace_messages(session)
            # Nochmal kompaktieren mit weniger keep_last (1 Round in Notfall)
            await sessions.compact(project_id, summary, keep_last=4, keep_last_rounds=1)
            _metrics.record_compaction(project_id, stage=3)
            logger.info(
                "Full-Compaction abgeschlossen (Projekt: %s, ~%d Tokens)",
                project_id, sessions.estimated_tokens(project_id),
            )

        # Memory Flush: Summary in Memory-Datei schreiben
        # damit zukünftige Sessions relevante Fakten via BM25 finden
        if boss_cfg.agent_dir:
            _flush_summary_to_memory(boss_cfg.agent_dir, summary)

        # #467: Post-Compact Reinjektion — zuletzt bearbeitete Files wieder einfügen
        if _edited_files:
            reinject = _build_reinject_context(_edited_files)
            if reinject:
                session = sessions.get_active(project_id)
                if session:
                    from .session_manager import Message as _Msg
                    reinject_msg = _Msg.create(role=MessageRole.SYSTEM, content=reinject)
                    # Nach Summary (Index 0), vor den behaltenen Messages einfügen
                    session.messages.insert(1, reinject_msg)
                    sessions._db_replace_messages(session)
                    logger.info(
                        "Post-compact reinject: %d Files reinjiziert (Projekt: %s)",
                        len(_edited_files), project_id,
                    )

    except Exception as e:
        logger.warning("Context-Kompaktierung fehlgeschlagen: %s", e)
        current_tokens = sessions.estimated_tokens(project_id)
        if current_tokens > 80_000:  # Emergency Reset nur bei wirklichem Overflow
            logger.error(
                "Context-Notfall-Reset (Projekt: %s, ~%d geschätzte Tokens > 40k)",
                project_id, current_tokens,
            )
            await sessions.new_session(project_id)
