"""
router_knowledge.py — Wissens-Suche (A-MEM + lokale Dokumente)

POST /admin/knowledge/search  → Durchsucht A-MEM Shared Memory
GET  /admin/knowledge/status  → Prüft ob A-MEM erreichbar ist
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends

logger = logging.getLogger(__name__)

AMEM_SEARCH_URL = "http://127.0.0.1:8021/api/search"
AMEM_STATS_URL = "http://127.0.0.1:8021/api/stats"


def _curl_json(url: str, method: str = "GET", data: dict | None = None, timeout: int = 10) -> dict | None:
    """Einfacher HTTP-Request ohne httpx-Dependency (curl via subprocess)."""
    import subprocess
    cmd = ["curl", "-sf", "--noproxy", "*", "-m", str(timeout)]
    if method == "POST" and data:
        cmd += ["-X", "POST", "-H", "Content-Type: application/json", "-d", json.dumps(data)]
    cmd.append(url)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout + 2)
        if r.returncode == 0 and r.stdout:
            return json.loads(r.stdout)
    except Exception as e:
        logger.debug("curl %s fehlgeschlagen: %s", url, e)
    return None


def register_knowledge_routes(
    admin_router: APIRouter,
    *,
    require_admin,
) -> None:

    @admin_router.get("/admin/knowledge/status")
    def knowledge_status(_a=Depends(require_admin)):
        """Prüft A-MEM Erreichbarkeit und gibt Stats zurück."""
        stats = _curl_json(AMEM_STATS_URL)
        if stats:
            return {
                "available": True,
                "total_notes": stats.get("total_notes", 0),
                "embedding_model": stats.get("embedding_model", ""),
                "llm_model": stats.get("llm_model", ""),
            }
        return {"available": False, "total_notes": 0}

    @admin_router.post("/admin/knowledge/search")
    def knowledge_search(body: dict, _a=Depends(require_admin)):
        """Durchsucht A-MEM Shared Memory.

        Body: {"query": str, "mode": "hybrid"|"agentic", "limit": int}
        """
        query = body.get("query", "").strip()
        if not query:
            return {"results": [], "error": "Kein Suchbegriff"}

        mode = body.get("mode", "hybrid")
        limit = min(body.get("limit", 10), 50)

        data = _curl_json(AMEM_SEARCH_URL, method="POST", data={
            "query": query,
            "mode": mode,
            "limit": limit,
        })

        if data is None:
            return {"results": [], "error": "A-MEM nicht erreichbar — ist der Service gestartet?"}

        results = []
        for r in data.get("results", []):
            results.append({
                "id": r.get("id", ""),
                "content": r.get("content", "")[:500],
                "score": r.get("score"),
                "keywords": r.get("keywords", []),
                "category": r.get("category", ""),
                "tags": r.get("tags", []),
                "context": r.get("context", ""),
            })

        return {
            "results": results,
            "total": len(results),
            "mode": mode,
            "query": query,
        }
