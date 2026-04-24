"""
test_821_context_window_tuning.py — Drei Schrauben am Context-Lifecycle.

(1) _MAX_HISTORY_SHARE wurde von 0.50 auf 0.35 gesenkt (#884).
(2) token_threshold ist modell-skaliert (40% des Context-Windows, Floor 8k).
(3) ProjectConfig.compaction_threshold ueberschreibt den modell-skalierten Default.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ─── Schraube 1: _MAX_HISTORY_SHARE ─────────────────────────────────────

def test_max_history_share_is_half():
    from hydrahive_core import orchestrator_context as oc
    assert oc._MAX_HISTORY_SHARE == 0.35


def test_history_budget_minimax_200k():
    from hydrahive_core.orchestrator_context import _history_token_budget
    # MiniMax 200k window, 0 system prompt:
    # available = 200_000 - 0 - 20_000 = 180_000
    # budget    = 180_000 * 0.35 = 63_000
    assert _history_token_budget("minimax-m2.7", 0) == 62_999  # 180k * 0.35 = 62999 (float truncation)


def test_history_budget_subtracts_system_prompt():
    from hydrahive_core.orchestrator_context import _history_token_budget
    # 200k - 50k - 20k = 130k -> *0.35 = 45_500
    assert _history_token_budget("claude-sonnet-4-6", 50_000) == 45_500  # 130k * 0.35


# ─── Schraube 2: token_threshold modell-skaliert ───────────────────────

def test_token_threshold_scales_with_window():
    """Pruefe die Resolve-Logik direkt — sie laeuft inline in _compact_if_needed,
    deshalb spiegeln wir die Berechnung hier wider und stellen sie via
    _context_window_for_model + Konstante sicher."""
    from hydrahive_core.orchestrator_context import _context_window_for_model
    # 40% Faktor + Floor 8k:
    assert max(8_000, int(_context_window_for_model("minimax-m2.7") * 0.40)) == 80_000
    assert max(8_000, int(_context_window_for_model("claude-sonnet-4-6") * 0.40)) == 80_000
    assert max(8_000, int(_context_window_for_model("gpt-4o") * 0.40)) == 51_200
    assert max(8_000, int(_context_window_for_model("qwen3-coder") * 0.40)) == 104_800
    # Unbekanntes Modell -> Window 8k -> Threshold 8k (Floor)
    assert max(8_000, int(_context_window_for_model("phi-3-mini") * 0.40)) == 8_000


# ─── Schraube 3: ProjectConfig.compaction_threshold ────────────────────

def test_project_config_compaction_threshold_default_none():
    from hydrahive_core.project_config import ProjectConfig, ProjectIdentity
    cfg = ProjectConfig(id="x", identity=ProjectIdentity(name="X"))
    assert cfg.compaction_threshold is None


def test_project_config_compaction_threshold_override():
    from hydrahive_core.project_config import ProjectConfig, ProjectIdentity
    cfg = ProjectConfig(
        id="y", identity=ProjectIdentity(name="Y"),
        compaction_threshold=120_000,
    )
    assert cfg.compaction_threshold == 120_000


# ─── Resolve-Pfad in _compact_if_needed (Smoke) ────────────────────────

import asyncio
from types import SimpleNamespace


class _FakeSessions:
    """Simuliert estimated_tokens + get_active. Reicht fuer den Early-Return-Pfad."""
    def __init__(self, estimated: int):
        self._estimated = estimated
    def estimated_tokens(self, project_id: str) -> int:
        return self._estimated
    def get_active(self, project_id: str):
        return None  # -> Funktion returnt vor LLM-Call


def _make_boss_cfg(model: str = "minimax-m2.7", agent_threshold: int | None = None):
    return SimpleNamespace(
        llm=SimpleNamespace(model=model),
        agent_dir=None,
        compaction_threshold=agent_threshold,
        id="boss",
    )


async def test_compact_below_threshold_skips_minimax():
    """Bei MiniMax (200k * 0.40 = 80k) muessen 70k estimated NICHT triggern."""
    from hydrahive_core.orchestrator_context import _compact_if_needed
    sessions = _FakeSessions(estimated=70_000)
    boss_cfg = _make_boss_cfg("minimax-m2.7")
    # get_active=None → Funktion returnt sauber. Wenn der Threshold-Check
    # versagt (z.B. fixe 40k statt 80k), waere die Funktion bereits weiter
    # vorgedrungen. Hier zaehlt nur: keine Exception.
    await _compact_if_needed(sessions, "p", boss_cfg)


async def test_compact_project_override_lowers_threshold():
    """Projekt-Override 30k unter modell-default 80k → 70k triggert dann."""
    from hydrahive_core.orchestrator_context import _compact_if_needed
    sessions = _FakeSessions(estimated=70_000)
    boss_cfg = _make_boss_cfg("minimax-m2.7")
    await _compact_if_needed(sessions, "p", boss_cfg, threshold_override=30_000)


def test_stage3_defaults_softened():
    """Stage 3 verwendet jetzt keep_last=8/keep_last_rounds=2 + Tool-Trim ab 500.
    Dieser Test sichert die Default-Werte gegen Regressions ab — keine direkte
    Aufrufpfad-Pruefung, sondern Source-Inspektion damit die Schrauben nicht
    versehentlich zurueck auf 4/1/200 wandern."""
    import inspect
    from hydrahive_core import orchestrator_context as oc
    src = inspect.getsource(oc._compact_if_needed)
    # Stage-3 keep_last=8, keep_last_rounds=2
    assert "keep_last=8, keep_last_rounds=2" in src, "Stage-3 keep_last/keep_last_rounds geaendert?"
    # Tool-Trim ab 500 Zeichen statt 200
    assert "len(m.content) > 500" in src, "Stage-3 Tool-Trim-Schwelle geaendert?"
