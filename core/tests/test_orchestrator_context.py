"""
test_orchestrator_context.py — Tests für orchestrator_context.py

Testet standalone-Funktionen:
- _context_mode: normal vs. full
- build_system_prompt (#636): einziger autoritativer Builder, Tuple-Return
- _compact_if_needed: Threshold-Check, LLM-Summary, Notfall-Reset
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.orchestrator_context import (
    _context_mode,
    build_system_prompt,
    _compact_if_needed,
)


async def _build_prompt_str(cfg, user_text: str) -> str:
    """Test-Helfer: Tuple-Builder zu String joinen wie call-sites es tun."""
    static_p, dynamic_p = await build_system_prompt(cfg, user_text)
    return (static_p + "\n\n" + dynamic_p).strip() if dynamic_p else static_p


# ================================================================= _context_mode

class TestContextMode:

    def test_normal_fuer_allgemeine_fragen(self):
        assert _context_mode("Was ist das Wetter?") == "normal"
        assert _context_mode("Hilf mir mit Python")  == "normal"
        assert _context_mode("")                      == "normal"

    def test_full_bei_explicit_prefix(self):
        assert _context_mode("!full zeig mir alles") == "full"
        assert _context_mode("!full schau dir das an") == "full"

    def test_full_bei_deep_dive_trigger(self):
        assert _context_mode("deep dive in den code")  == "full"
        assert _context_mode("deep-dive analyse")       == "full"

    def test_full_bei_analysiere_alles(self):
        assert _context_mode("analysiere alles vollständig") == "full"
        assert _context_mode("zeig mir alles")               == "full"

    def test_full_bei_full_context(self):
        assert _context_mode("full context bitte")           == "full"
        assert _context_mode("vollständiger kontext")        == "full"

    def test_gross_kleinschreibung_ignoriert(self):
        assert _context_mode("DEEP DIVE alles") == "full"
        assert _context_mode("Analysiere Alles") == "full"


# ================================================================= _build_system_prompt

def _make_agent_cfg(agent_dir=None, soul=None, identity="Test-Agent", tools=None):
    cfg = MagicMock()
    cfg.identity = identity
    cfg.soul = soul
    cfg.agent_dir = Path(agent_dir) if agent_dir else None
    cfg.tools = tools or []
    cfg.id = "test-agent"
    cfg.llm.provider = "nvidia"
    cfg.llm.model = "qwen/qwen3-coder-480b-a35b-instruct"
    return cfg


class TestBuildSystemPrompt:

    async def test_identity_immer_enthalten(self, tmp_path):
        cfg = _make_agent_cfg(agent_dir=str(tmp_path))
        prompt = await _build_prompt_str(cfg, "Hallo")
        assert "Test-Agent" in prompt

    async def test_kein_memory_ohne_verzeichnis(self, tmp_path):
        cfg = _make_agent_cfg(agent_dir=str(tmp_path))
        prompt = await _build_prompt_str(cfg, "Hallo")
        assert "Persistentes Gedächtnis" not in prompt

    async def test_memory_budget_normal_begrenzt(self, tmp_path):
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        (mem_dir / "gross.md").write_text("X" * 50_000)
        cfg = _make_agent_cfg(agent_dir=str(tmp_path))
        prompt = await _build_prompt_str(cfg, "Normale Frage")
        assert len(prompt) < 35_000

    async def test_memory_bm25_snippet_erscheint_in_prompt(self, tmp_path):
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        (mem_dir / "fakt.md").write_text("Lilith ist der wichtigste Agent und kennt das Projekt.")
        cfg = _make_agent_cfg(agent_dir=str(tmp_path))
        prompt = await _build_prompt_str(cfg, "Wer ist Lilith?")
        assert "Lilith" in prompt or "Persistentes" in prompt

    async def test_full_mode_mehr_bm25_treffer(self, tmp_path):
        """full-mode verwendet k=8 BM25-Treffer statt k=4 — prompt kann länger werden."""
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        for i in range(10):
            (mem_dir / f"mem_{i:02d}.md").write_text(f"Faktum {i}: Dies ist ein wichtiger Eintrag über das Projekt.")
        cfg = _make_agent_cfg(agent_dir=str(tmp_path))
        normal = await _build_prompt_str(cfg, "Was ist das Projekt?")
        full   = await _build_prompt_str(cfg, "!full Was ist das Projekt?")
        # full kann gleich oder länger sein — mindestens kein Fehler
        assert isinstance(full, str) and isinstance(normal, str)

    async def test_soul_wird_eingebunden(self, tmp_path):
        soul_file = tmp_path / "soul.md"
        soul_file.write_text("Ich bin ein freundlicher Agent.")
        cfg = _make_agent_cfg(agent_dir=str(tmp_path), soul="soul.md")
        prompt = await _build_prompt_str(cfg, "Test")
        assert "freundlicher Agent" in prompt

    async def test_soul_datei_fehlt_kein_fehler(self, tmp_path):
        cfg = _make_agent_cfg(agent_dir=str(tmp_path), soul="nicht_vorhanden.md")
        prompt = await _build_prompt_str(cfg, "Test")
        assert "Test-Agent" in prompt

    async def test_runtime_modell_ueberschreibt_stale_memory_hinweis(self, tmp_path):
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        (mem_dir / "session-summary-2026-04-17.md").write_text(
            "Das System läuft auf Claude 3.5 Sonnet.",
            encoding="utf-8",
        )
        cfg = _make_agent_cfg(agent_dir=str(tmp_path))
        prompt = await _build_prompt_str(cfg, "Welches Modell nutzt du?")
        assert "## Runtime-LLM" in prompt
        assert "qwen/qwen3-coder-480b-a35b-instruct" in prompt
        assert "veraltet" in prompt


# ================================================================= _compact_if_needed

class TestCompactIfNeeded:

    def _make_sessions(self, estimated_tokens=100, active_messages=10):
        sessions = MagicMock()
        sessions.estimated_tokens.return_value = estimated_tokens
        mock_session = MagicMock()
        mock_session.messages = [MagicMock() for _ in range(active_messages)]
        sessions.get_active.return_value = mock_session
        sessions.compact = AsyncMock()
        sessions.new_session = AsyncMock()
        return sessions

    def _make_boss_cfg(self, model="claude-3-5-sonnet-20241022"):
        cfg = MagicMock()
        cfg.llm.model = model
        cfg.llm.max_tokens = 400
        cfg.compaction_threshold = None  # #416: kein Agent-Override
        cfg.agent_dir = None
        return cfg

    async def test_unter_schwellwert_keine_aktion(self):
        sessions = self._make_sessions(estimated_tokens=1_000)
        boss_cfg = self._make_boss_cfg()
        await _compact_if_needed(sessions, "proj", boss_cfg)
        sessions.compact.assert_not_called()
        sessions.new_session.assert_not_called()

    async def test_lokales_modell_niedrigerer_schwellwert(self):
        # Unbekanntes Modell → ctx_window-Fallback 8k → threshold = max(8k, 8k*0.4) = 8k
        # 10k > 8k → kompaktieren.
        sessions = self._make_sessions(estimated_tokens=10_000)
        boss_cfg = self._make_boss_cfg(model="some-unknown-tiny-model")
        with patch("hydrahive_core.orchestrator_llm._llm_with_retry", new_callable=AsyncMock) as mock_retry:
            mock_resp = MagicMock()
            mock_resp.choices[0].message.content = "Zusammenfassung"
            mock_retry.return_value = mock_resp
            await _compact_if_needed(sessions, "proj", boss_cfg)
        assert sessions.compact.call_count >= 1

    async def test_zu_wenig_nachrichten_keine_kompaktierung(self):
        sessions = self._make_sessions(estimated_tokens=25_000, active_messages=3)
        boss_cfg = self._make_boss_cfg()
        await _compact_if_needed(sessions, "proj", boss_cfg)
        sessions.compact.assert_not_called()

    async def test_notfall_reset_bei_kompaktierungsfehler_und_zu_gross(self):
        sessions = self._make_sessions(estimated_tokens=90_000, active_messages=20)
        # Nach Fehler: immer noch > 80k → Emergency Reset
        sessions.estimated_tokens.side_effect = [90_000, 90_000]
        boss_cfg = self._make_boss_cfg()
        with patch("hydrahive_core.orchestrator_llm._llm_with_retry", side_effect=Exception("LLM down")):
            await _compact_if_needed(sessions, "proj", boss_cfg)
        sessions.new_session.assert_called_once_with("proj")
