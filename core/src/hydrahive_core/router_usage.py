"""
router_usage.py — Token-Usage & Kosten-Statistiken

Liest aggregierte Token-Daten aus den Session-Dateien und berechnet
API-Kosten basierend auf bekannten Anthropic-Preisen ($/1M Tokens).
"""
from __future__ import annotations

from fastapi import APIRouter

from .session_manager import SessionManager

# Anthropic Pricing Stand 2026-03 (USD pro 1M Tokens)
_PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5": {
        "input": 0.80, "output": 4.00,
        "cache_write": 1.00, "cache_read": 0.08,
    },
    "claude-sonnet-4-5": {
        "input": 3.00, "output": 15.00,
        "cache_write": 3.75, "cache_read": 0.30,
    },
    "claude-sonnet-4-6": {
        "input": 3.00, "output": 15.00,
        "cache_write": 3.75, "cache_read": 0.30,
    },
    "claude-opus-4-5": {
        "input": 15.00, "output": 75.00,
        "cache_write": 18.75, "cache_read": 1.50,
    },
    "claude-opus-4-6": {
        "input": 15.00, "output": 75.00,
        "cache_write": 18.75, "cache_read": 1.50,
    },
    # OpenAI Pricing Stand 2026-04
    "gpt-4o": {
        "input": 2.50, "output": 10.00,
        "cache_write": 2.50, "cache_read": 1.25,
    },
    "gpt-4o-mini": {
        "input": 0.15, "output": 0.60,
        "cache_write": 0.15, "cache_read": 0.075,
    },
    "o3": {
        "input": 10.00, "output": 40.00,
        "cache_write": 10.00, "cache_read": 5.00,
    },
    "o3-mini": {
        "input": 1.10, "output": 4.40,
        "cache_write": 1.10, "cache_read": 0.55,
    },
    # Google Gemini Pricing Stand 2026-04
    "gemini-2.0-flash": {
        "input": 0.10, "output": 0.40,
        "cache_write": 0.10, "cache_read": 0.025,
    },
    "gemini-2.5-pro": {
        "input": 1.25, "output": 10.00,
        "cache_write": 1.25, "cache_read": 0.315,
    },
    # DeepSeek
    "deepseek-r1": {
        "input": 0.55, "output": 2.19,
        "cache_write": 0.55, "cache_read": 0.14,
    },
}

_PRICING_FALLBACK = {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30}


def _model_key(model: str) -> str:
    """Normalisiert Modellnamen → Pricing-Key."""
    for prefix in ("anthropic/", "openai/", "claude/"):
        if model.startswith(prefix):
            model = model[len(prefix):]
    return model


def _calc_cost(model: str, inp: int, out: int, cw: int, cr: int) -> dict[str, float]:
    pricing = _PRICING.get(_model_key(model), _PRICING_FALLBACK)
    factor = 1 / 1_000_000
    return {
        "input":       round(inp * pricing["input"]       * factor, 6),
        "output":      round(out * pricing["output"]      * factor, 6),
        "cache_write": round(cw  * pricing["cache_write"] * factor, 6),
        "cache_read":  round(cr  * pricing["cache_read"]  * factor, 6),
        "total": round(
            (inp * pricing["input"] + out * pricing["output"] +
             cw  * pricing["cache_write"] + cr * pricing["cache_read"]) * factor,
            6,
        ),
    }


def register_usage_routes(
    admin_router: APIRouter,
    *,
    sessions: SessionManager,
    agent_sessions: SessionManager,
) -> None:

    @admin_router.get("/admin/usage")
    async def get_usage():
        """
        Aggregierte Token-Usage aller Projekte inkl. API-Kostenschätzung.
        Liest aus session_manager — nur Nachrichten mit gespeicherten Token-Counts.
        """
        raw = sessions.get_usage_stats()
        agent_raw = agent_sessions.get_usage_stats()

        projects_out = []
        grand_total = {
            "input": 0, "output": 0,
            "cache_read": 0, "cache_write": 0,
            "cost": 0.0,
        }

        for source in (raw, agent_raw):
            for proj_id, stats in source.items():
                cost_by_model: dict[str, dict] = {}
                proj_total_cost = 0.0

                for mdl, mb in stats["model_breakdown"].items():
                    c = _calc_cost(mdl, mb["input"], mb["output"], mb["cache_write"], mb["cache_read"])
                    cost_by_model[mdl] = {
                        "tokens": mb,
                        "cost": c,
                    }
                    proj_total_cost += c["total"]

                # Fallback falls kein model_breakdown vorhanden
                if not cost_by_model:
                    proj_total_cost = _calc_cost(
                        "claude-sonnet-4-6",
                        stats["total_input"], stats["total_output"],
                        stats["total_cache_write"], stats["total_cache_read"],
                    )["total"]

                # #417: Cache Hit-Rate pro Projekt
                total_billable = stats["total_input"] + stats["total_cache_write"] + stats["total_cache_read"]
                cache_hit_rate = round(stats["total_cache_read"] / total_billable * 100, 1) if total_billable > 0 else 0.0

                projects_out.append({
                    "project_id":           proj_id,
                    "total_input":          stats["total_input"],
                    "total_output":         stats["total_output"],
                    "total_cache_read":     stats["total_cache_read"],
                    "total_cache_write":    stats["total_cache_write"],
                    "sessions_with_usage":  stats["sessions_with_usage"],
                    "total_cost":           round(proj_total_cost, 6),
                    "cache_hit_rate":       cache_hit_rate,
                    "model_breakdown":      cost_by_model,
                })

                grand_total["input"]       += stats["total_input"]
                grand_total["output"]      += stats["total_output"]
                grand_total["cache_read"]  += stats["total_cache_read"]
                grand_total["cache_write"] += stats["total_cache_write"]
                grand_total["cost"]        += proj_total_cost

        grand_total["cost"] = round(grand_total["cost"], 6)
        gt_billable = grand_total["input"] + grand_total["cache_write"] + grand_total["cache_read"]
        grand_total["cache_hit_rate"] = round(grand_total["cache_read"] / gt_billable * 100, 1) if gt_billable > 0 else 0.0

        return {
            "projects":    projects_out,
            "grand_total": grand_total,
            "pricing_ref": _PRICING,
        }
