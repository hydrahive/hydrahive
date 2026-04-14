"""
prefetch.py — Async Memory-Prefetch (Issue #625)

Startet BM25-Index-Update + Memory-Search + A-MEM-Suche als Hintergrund-Tasks
direkt am User-Turn-Anfang. Der Prompt-Builder awaitet sie kurz vor dem
LLM-Call (Timeout-gesichert) statt synchron blockierend zu suchen.

Vorher (orchestrator_context.py:343-349, 639-650):
    await loop.run_in_executor(None, update_memory_index, ...)
    snippets = await loop.run_in_executor(None, lambda: search_memory(...))
    # → blockiert Turn-Start bis BM25 fertig

Nachher (orchestrator.py / orchestrator_stream.py):
    pf = start_memory_prefetch(agent_dir, user_text)
    # ... restlicher Build läuft parallel ...
    snippets = await pf.get_bm25(timeout=0.8)
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 0.8  # Sekunden — wenn Memory-Suche länger braucht: ohne weitermachen


class MemoryPrefetch:
    """Hält asyncio-Tasks für BM25 + A-MEM, awaitbar mit Timeout."""

    def __init__(self, agent_dir: Optional[Path], user_text: str, *, k: int = 4):
        self.agent_dir = agent_dir
        self.user_text = user_text or ""
        self.k = k
        self.started_at = time.monotonic()
        self.bm25_task: Optional[asyncio.Task] = None
        self.amem_task: Optional[asyncio.Task] = None

        if agent_dir and self.user_text.strip():
            self.bm25_task = asyncio.create_task(self._run_bm25())
            self.amem_task = asyncio.create_task(self._run_amem())

    async def _run_bm25(self) -> list[str]:
        from .memory_search import search_memory, update_index as update_memory_index
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, update_memory_index, self.agent_dir)
            return await loop.run_in_executor(
                None, lambda: search_memory(self.agent_dir, self.user_text, k=self.k)
            )
        except Exception as e:
            logger.debug("Prefetch BM25 Fehler: %s", e)
            return []

    async def _run_amem(self) -> str:
        from .orchestrator_context import _amem_global_search
        try:
            return await _amem_global_search(self.user_text)
        except Exception as e:
            logger.debug("Prefetch A-MEM Fehler: %s", e)
            return ""

    async def get_bm25(self, *, timeout: float = _DEFAULT_TIMEOUT) -> list[str]:
        if not self.bm25_task:
            return []
        try:
            result = await asyncio.wait_for(asyncio.shield(self.bm25_task), timeout)
            elapsed_ms = int((time.monotonic() - self.started_at) * 1000)
            logger.debug("Prefetch BM25 ready nach %dms", elapsed_ms)
            return result or []
        except asyncio.TimeoutError:
            elapsed_ms = int((time.monotonic() - self.started_at) * 1000)
            logger.info("Prefetch BM25 timeout (%dms > %.0fms) — fahre ohne Memory fort",
                        elapsed_ms, timeout * 1000)
            return []

    async def get_amem(self, *, timeout: float = _DEFAULT_TIMEOUT) -> str:
        if not self.amem_task:
            return ""
        try:
            result = await asyncio.wait_for(asyncio.shield(self.amem_task), timeout)
            return result or ""
        except asyncio.TimeoutError:
            elapsed_ms = int((time.monotonic() - self.started_at) * 1000)
            logger.info("Prefetch A-MEM timeout (%dms) — überspringe", elapsed_ms)
            return ""

    def cancel(self) -> None:
        for t in (self.bm25_task, self.amem_task):
            if t and not t.done():
                t.cancel()


def start_memory_prefetch(
    agent_dir: Optional[Path],
    user_text: str,
    *,
    k: int = 4,
) -> MemoryPrefetch:
    """Startet BM25 + A-MEM Prefetch als Hintergrund-Tasks. Sofort returned.

    Awaite Treffer später via `get_bm25(timeout=…)` / `get_amem(timeout=…)`.
    """
    return MemoryPrefetch(agent_dir, user_text, k=k)
