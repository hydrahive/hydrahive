"""
auto_dream.py — Background Memory-Konsolidierung (inspiriert von Claude Code autoDream)

Periodisch (alle 24h oder nach N Sessions) reviewed ein LLM-Call die
Transcripts eines Agenten und konsolidiert Erkenntnisse in Memory-Files.

Gate-Reihenfolge (billigster Check zuerst):
  1. Zeit: Stunden seit letztem Dream >= min_hours
  2. Sessions: Transcript-Count mit mtime > letztem Dream >= min_sessions
  3. Lock: Kein anderer Dream-Prozess aktiv

Pattern analog zu Claude Code autoDream.ts, adaptiert für HydraHive.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine

from .settings import settings

logger = logging.getLogger(__name__)

_STATE_FILENAME = ".dream_state.json"
_LOCK_FILENAME = ".dream_lock"

DEFAULT_CONFIG = {
    "enabled": True,
    "min_hours": 12,
    "min_sessions": 1,
    "check_interval_seconds": 600,  # alle 10 Min prüfen
    "max_transcript_chars": 60000,
    "summary_model": "claude-haiku-4-5-20251001",
}


def _load_dream_config() -> dict:
    cfg_path = settings.etc_dir / "auto_dream.json"
    if cfg_path.exists():
        try:
            return {**DEFAULT_CONFIG, **json.loads(cfg_path.read_text())}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_dream_config(cfg: dict) -> None:
    cfg_path = settings.etc_dir / "auto_dream.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, indent=2))


# ── Per-Agent State ──────────────────────────────────────────────────────────

def _read_dream_state(agent_dir: Path) -> dict:
    state_path = agent_dir / _STATE_FILENAME
    try:
        if state_path.exists():
            return json.loads(state_path.read_text())
    except Exception:
        pass
    return {"last_dream_at": 0, "dream_count": 0, "last_sessions_reviewed": 0}


def _write_dream_state(agent_dir: Path, state: dict) -> None:
    state_path = agent_dir / _STATE_FILENAME
    state_path.write_text(json.dumps(state, indent=2))


def _try_acquire_lock(agent_dir: Path) -> bool:
    lock_path = agent_dir / _LOCK_FILENAME
    if lock_path.exists():
        # Stale Lock? Älter als 10 Minuten = freigeben
        try:
            if time.time() - lock_path.stat().st_mtime > 600:
                lock_path.unlink(missing_ok=True)
            else:
                return False
        except Exception:
            return False
    try:
        lock_path.write_text(str(int(time.time())))
        return True
    except Exception:
        return False


def _release_lock(agent_dir: Path) -> None:
    (agent_dir / _LOCK_FILENAME).unlink(missing_ok=True)


# ── Transcript-Sammlung ──────────────────────────────────────────────────────

def _collect_transcripts(agent_dir: Path, since_ts: float, max_chars: int) -> tuple[list[str], int]:
    """Sammelt Transcripts seit since_ts. Gibt (Texte, Anzahl) zurück."""
    transcripts_dir = agent_dir / "transcripts"
    if not transcripts_dir.exists():
        return [], 0

    files = sorted(transcripts_dir.glob("*.md"), key=lambda p: p.stat().st_mtime)
    recent = [f for f in files if f.stat().st_mtime > since_ts]
    if not recent:
        return [], 0

    texts = []
    total_chars = 0
    for f in recent:
        try:
            text = f.read_text(encoding="utf-8")
            if total_chars + len(text) > max_chars:
                text = text[:max_chars - total_chars]
                texts.append(text)
                total_chars += len(text)
                break
            texts.append(text)
            total_chars += len(text)
        except Exception:
            continue

    return texts, len(recent)


# ── Dream-Prompt ─────────────────────────────────────────────────────────────

def _build_dream_prompt(agent_id: str, transcripts: list[str], existing_memory: str) -> str:
    transcript_block = "\n\n---\n\n".join(transcripts)
    return f"""Du bist ein Memory-Konsolidierer für den Agenten '{agent_id}'.

Deine Aufgabe: Analysiere die folgenden Session-Transcripts und extrahiere
wichtige Erkenntnisse, Muster und Fakten die der Agent sich merken sollte.

## Regeln
- Schreibe auf Deutsch
- Fokus auf: User-Präferenzen, wiederkehrende Probleme, gelöste Issues, technische Fakten
- Ignoriere: Smalltalk, Wiederholungen, triviale Informationen
- Format: Bullet-Points, kompakt, max 800 Wörter
- Wenn keine neuen Erkenntnisse: antworte nur "KEINE_NEUEN_ERKENNTNISSE"

## Bestehende Memory (nicht wiederholen!)
{existing_memory[:3000] if existing_memory else "(leer)"}

## Session-Transcripts seit letztem Dream
{transcript_block}

## Output
Fasse die wichtigsten neuen Erkenntnisse zusammen:"""


# ── Dream-Ausführung ─────────────────────────────────────────────────────────

async def _run_dream_for_agent(
    agent_id: str,
    agent_dir: Path,
    cfg: dict,
    notify_fn: Callable[..., Coroutine] | None = None,
) -> dict:
    """Führt einen Dream-Zyklus für einen einzelnen Agenten aus."""
    state = _read_dream_state(agent_dir)
    last_dream = state.get("last_dream_at", 0)

    # Gate 1: Zeit
    hours_since = (time.time() - last_dream) / 3600
    min_hours = cfg.get("min_hours", 24)
    if hours_since < min_hours:
        return {"skipped": True, "reason": f"zu früh ({hours_since:.1f}h < {min_hours}h)"}

    # Gate 2: Sessions
    max_chars = cfg.get("max_transcript_chars", 60000)
    transcripts, session_count = _collect_transcripts(agent_dir, last_dream, max_chars)
    min_sessions = cfg.get("min_sessions", 3)
    if session_count < min_sessions:
        return {"skipped": True, "reason": f"zu wenige Sessions ({session_count} < {min_sessions})"}

    # Gate 3: Lock
    if not _try_acquire_lock(agent_dir):
        return {"skipped": True, "reason": "lock aktiv"}

    try:
        # Bestehende Memory lesen
        memory_dir = agent_dir / "memory"
        existing_memory = ""
        learned_path = memory_dir / "learned-facts.md"
        if learned_path.exists():
            existing_memory = learned_path.read_text(encoding="utf-8")[-3000:]

        # Dream-Prompt bauen
        prompt = _build_dream_prompt(agent_id, transcripts, existing_memory)

        # LLM-Call
        from .orchestrator import _load_claude_oauth_token
        oauth_token = _load_claude_oauth_token()
        if not oauth_token:
            return {"skipped": True, "reason": "kein OAuth-Token"}

        import anthropic
        from .provider_config import ANTHROPIC_OAUTH_HEADERS
        client = anthropic.AsyncAnthropic(
            api_key="",
            auth_token=oauth_token,
            default_headers=ANTHROPIC_OAUTH_HEADERS,
        )
        model = cfg.get("summary_model", "claude-haiku-4-5-20251001")
        resp = await client.messages.create(
            model=model,
            max_tokens=1500,
            system=[{"type": "text", "text": "Du bist ein präziser Memory-Konsolidierer. Antworte knapp und strukturiert."}],
            messages=[{"role": "user", "content": prompt}],
        )
        summary = (resp.content[0].text if resp.content else "").strip()

        if not summary or "KEINE_NEUEN_ERKENNTNISSE" in summary:
            # State trotzdem updaten damit wir nicht jedes Mal neu prüfen
            state["last_dream_at"] = time.time()
            _write_dream_state(agent_dir, state)
            return {"completed": True, "agent_id": agent_id, "result": "keine neuen Erkenntnisse", "sessions_reviewed": session_count}

        # Erkenntnisse in learned-facts.md speichern
        from .learning_memory import append_learning_snapshot
        append_learning_snapshot(
            agent_dir,
            summary,
            source="auto_dream",
            logger=logger,
        )

        # State updaten
        state["last_dream_at"] = time.time()
        state["dream_count"] = state.get("dream_count", 0) + 1
        state["last_sessions_reviewed"] = session_count
        _write_dream_state(agent_dir, state)

        logger.info("AutoDream für '%s': %d Sessions → %d Zeichen Erkenntnisse",
                     agent_id, session_count, len(summary))

        # Notification
        if notify_fn:
            try:
                await notify_fn(
                    user="admin",
                    type="auto_dream",
                    title=f"AutoDream: {agent_id}",
                    body=f"{session_count} Sessions konsolidiert. {len(summary)} Zeichen neue Erkenntnisse.",
                    link=f"/agents/{agent_id}/chat",
                )
            except Exception:
                pass

        return {
            "completed": True,
            "agent_id": agent_id,
            "sessions_reviewed": session_count,
            "summary_length": len(summary),
            "dream_count": state["dream_count"],
        }

    except Exception as e:
        logger.error("AutoDream für '%s' fehlgeschlagen: %s", agent_id, e)
        return {"error": str(e), "agent_id": agent_id}
    finally:
        _release_lock(agent_dir)


# ── Service ──────────────────────────────────────────────────────────────────

class AutoDreamService:
    """Background-Task der periodisch Agenten-Memories konsolidiert."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._agents_dir: Path = Path("/agents")
        self._notify_fn: Callable[..., Coroutine] | None = None

    def start(self, *, agents_dir: str | Path, notify_fn: Callable[..., Coroutine] | None = None) -> None:
        self._agents_dir = Path(agents_dir)
        self._notify_fn = notify_fn
        self._task = asyncio.create_task(self._loop(), name="auto-dream")
        logger.info("AutoDreamService gestartet")

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def run_now(self, agent_id: str | None = None) -> dict:
        """Dream sofort ausführen — für API-Trigger."""
        cfg = _load_dream_config()
        if agent_id:
            agent_dir = self._agents_dir / agent_id
            if not agent_dir.exists():
                return {"error": f"Agent '{agent_id}' nicht gefunden"}
            # Override Gates für manuellen Trigger
            override_cfg = {**cfg, "min_hours": 0, "min_sessions": 0}
            return await _run_dream_for_agent(agent_id, agent_dir, override_cfg, self._notify_fn)

        # Alle Agenten
        results = []
        for agent_dir in sorted(self._agents_dir.iterdir()):
            if not agent_dir.is_dir() or not (agent_dir / "agent.yaml").exists():
                continue
            result = await _run_dream_for_agent(agent_dir.name, agent_dir, cfg, self._notify_fn)
            results.append(result)
        return {"agents": results, "total": len(results)}

    async def _loop(self) -> None:
        await asyncio.sleep(120)  # 2 Min nach Start warten
        while True:
            try:
                cfg = _load_dream_config()
                if not cfg.get("enabled", True):
                    await asyncio.sleep(300)
                    continue

                interval = cfg.get("check_interval_seconds", 600)

                for agent_dir in sorted(self._agents_dir.iterdir()):
                    if not agent_dir.is_dir() or not (agent_dir / "agent.yaml").exists():
                        continue
                    try:
                        await _run_dream_for_agent(agent_dir.name, agent_dir, cfg, self._notify_fn)
                    except Exception as e:
                        logger.warning("AutoDream für '%s': %s", agent_dir.name, e)

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("AutoDreamService Fehler: %s", e)
                await asyncio.sleep(60)


auto_dream_service = AutoDreamService()
