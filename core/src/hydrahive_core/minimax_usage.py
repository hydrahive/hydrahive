"""minimax_usage.py — MiniMax Token-Plan / Usage API (#805 Phase A).

Endpoint: ``GET https://api.minimax.io/v1/token_plan/remains``
Header:   ``Authorization: Bearer <MINIMAX_API_KEY>``

Response liefert ms-epoch-Timestamps. Normalisieren für Frontend-Consumption.
30 s In-Module-Cache verhindert redundante HTTP-Calls (Frontend pollt alle 3 s).
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────── In-Module Cache

_cache: dict[str, Any] = {"data": None, "fetched_at": 0.0}
_CACHE_TTL = 30.0


# ────────────────────────────────────────────── Key-Lookup

def _minimax_api_key() -> str | None:
    """Delegiert an :func:`orchestrator_llm._minimax_api_key`."""
    from .orchestrator_llm import _minimax_api_key
    return _minimax_api_key()


# ────────────────────────────────────────────── Model-Name-Mapping

_MODEL_CATEGORIES: list[tuple[str, str]] = [
    ("MiniMax-M",      "text"),
    ("MiniMax-Hailuo", "video"),
    ("Hailuo",         "video"),
    ("speech",         "tts"),
    ("music",          "music"),
    ("image",          "image"),
]


def _short_name(model_name: str) -> str:
    for prefix, short in _MODEL_CATEGORIES:
        if model_name.startswith(prefix):
            return short
    return "misc"


# ────────────────────────────────────────────── Normalisierung


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_model(raw: dict) -> dict:
    """Normalisiert einen ``model_remains``-Eintrag für Frontend-Consumption."""
    now_ms = time.time() * 1000

    interval_total = int(raw.get("current_interval_total_count", 0))
    interval_used  = int(raw.get("current_interval_usage_count", 0))
    interval_pct   = (
        round(interval_used / interval_total * 100, 1)
        if interval_total else 0
    )

    weekly_total   = int(raw.get("current_weekly_total_count", 0))
    weekly_used    = int(raw.get("current_weekly_usage_count", 0))
    weekly_pct     = (
        round(weekly_used / weekly_total * 100, 1)
        if weekly_total else 0
    )

    end_time = raw.get("end_time")
    reset_in_s = (
        max(0, int((int(end_time) - now_ms) / 1000))
        if end_time else 0
    )

    return {
        "name":                _short_name(str(raw.get("model_name", ""))),
        "label":               str(raw.get("model_name", "")),
        "interval_total":      interval_total,
        "interval_used":       interval_used,
        "interval_pct":        min(interval_pct, 100),
        "interval_reset_in_s": reset_in_s,
        "weekly_total":        weekly_total,
        "weekly_used":         weekly_used,
        "weekly_pct":          min(weekly_pct, 100),
    }


# ────────────────────────────────────────────── Core Fetch

async def fetch_minimax_token_remains() -> dict:
    """Ruft MiniMax ``token_plan/remains`` ab, normalisiert + cached.

    Fehler oder fehlender Key → ``{"available": False, "reason": ...}``.
    """
    global _cache
    now = time.time()

    if _cache["data"] is not None and (now - _cache["fetched_at"]) < _CACHE_TTL:
        return _cache["data"]

    key = _minimax_api_key()
    if not key:
        data = {"available": False, "reason": "no_api_key", "fetched_at": _iso_now()}
        _cache = {"data": data, "fetched_at": now}
        return data

    import httpx as _httpx

    try:
        async with _httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.minimax.io/v1/token_plan/remains",
                headers={"Authorization": f"Bearer {key}"},
            )
    except Exception as exc:
        logger.warning("minimax_usage: HTTP fetch failed: %s", exc)
        data = {"available": False, "reason": "network_error", "fetched_at": _iso_now()}
        _cache = {"data": data, "fetched_at": now}
        return data

    if resp.status_code == 401:
        data = {"available": False, "reason": "invalid_api_key", "fetched_at": _iso_now()}
        _cache = {"data": data, "fetched_at": now}
        return data
    if resp.status_code >= 500:
        data = {"available": False, "reason": "upstream_error", "fetched_at": _iso_now()}
        _cache = {"data": data, "fetched_at": now}
        return data
    if resp.status_code >= 400:
        data = {"available": False, "reason": f"http_{resp.status_code}", "fetched_at": _iso_now()}
        _cache = {"data": data, "fetched_at": now}
        return data

    try:
        raw = resp.json()
    except Exception:
        data = {"available": False, "reason": "invalid_json", "fetched_at": _iso_now()}
        _cache = {"data": data, "fetched_at": now}
        return data

    model_list = raw.get("model_remains") if isinstance(raw, dict) else []
    if not isinstance(model_list, list):
        model_list = []
    models = [_normalize_model(m) for m in model_list if isinstance(m, dict)]

    data = {
        "available":  True,
        "fetched_at": _iso_now(),
        "models":     models,
    }
    _cache = {"data": data, "fetched_at": now}
    return data
