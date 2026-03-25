"""
test_orchestrator_context.py — Tests für orchestrator_context.py

Testet standalone-Funktionen:
- _context_mode: normal vs. full
- _build_system_prompt: Soul, Memory-Budget, Skills
- _compact_if_needed: Threshold-Check, LLM-Summary, Notfall-Reset
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.orchestrator_context import (
    _context_mode,
    _build_system_prompt,
    _compact_if_needed,
)


# ================================================================= _context_mode

class TestContextMode:

    def test_normal_fuer_allgemeine_fragen(self):
        assert _context_mode("Was ist das Wetter?") == "normal"
        assert _context_mode("Hilf mir mit Python")  == "normal"
        assert _context_mode("")                      == "normal"

    def test_full_bei_diff_trigger(self):
        assert _context_mode("zeig mir den diff zum letzten commit") == "full"
        assert _context_mode("diff zwischen zwei branches")          == "full"

    def test_full_bei_review_trigger(self):
        assert _context_mode("review mein letzter commit") == "full"
        assert _context_mode("review den PR bitte")        == "full"

    def test_full_bei_audit_trigger(self):
        assert _context_mode("audit dieser codebase")         == "full"
        assert _context_mode("analysiere alles vollständig")  == "full"

    def test_full_bei_pull_request_trigger(self):
        assert _context_mode("schau dir pull request an") == "full"
        assert _context_mode("kommentiere pr #42")        == "full"

    def test_gross_kleinschreibung_ignoriert(self):
        assert _context_mode("AUDIT alles") == "full"
        assert _context_mode("Review Mein Code") == "full"


# ================================================================= _build_system_prompt

def _make_agent_cfg(agent_dir=None, soul=None, identity="Test-Agent", tools=None):
    cfg = MagicMock()
    cfg.identity = identity
    cfg.soul = soul
    cfg.agent_dir = Path(agent_dir) if agent_dir else None
    cfg.tools = tools or []
    cfg.id = "test-agent"
    return cfg


class TestBuildSystemPrompt:

    def test_identity_immer_enthalten(self, tmp_path):
        cfg = _make_agent_cfg(agent_dir=str(tmp_path))
        prompt = _build_system_prompt(cfg, "Hallo")
        assert "Test-Agent" in prompt

    def test_kein_memory_ohne_verzeichnis(self, tmp_path):
        cfg = _make_agent_cfg(agent_dir=str(tmp_path))
        prompt = _build_system_prompt(cfg, "Hallo")
        assert "Persistentes Gedächtnis" not in prompt

    def test_memory_budget_normal_begrenzt(self, tmp_path):
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        (mem_dir / "gross.md").write_text("X" * 50_000)
        cfg = _make_agent_cfg(agent_dir=str(tmp_path))
        prompt = _build_system_prompt(cfg, "Normale Frage")
        assert len(prompt) < 35_000

    def test_per_file_cap_fuegt_kuerzungs_marker_ein(self, tmp_path):
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        (mem_dir / "mittel.md").write_text("A" * 15_000)
        cfg = _make_agent_cfg(agent_dir=str(tmp_path))
        prompt = _build_system_prompt(cfg, "Test")
        assert "gekürzt" in prompt or "Budget" in prompt

    def test_full_mode_laedt_mehr_als_normal(self, tmp_path):
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        for i in range(5):
            (mem_dir / f"mem_{i:02d}.md").write_text("B" * 20_000)
        cfg = _make_agent_cfg(agent_dir=str(tmp_path))
        normal = _build_system_prompt(cfg, "Normale Frage")
        full   = _build_system_prompt(cfg, "review mein letzter Code")
        assert len(full) > len(normal)

    def test_soul_wird_eingebunden(self, tmp_path):
        soul_file = tmp_path / "soul.md"
        soul_file.write_text("Ich bin ein freundlicher Agent.")
        cfg = _make_agent_cfg(agent_dir=str(tmp_path), soul="soul.md")
        prompt = _build_system_prompt(cfg, "Test")
        assert "freundlicher Agent" in prompt

    def test_soul_datei_fehlt_kein_fehler(self, tmp_path):
        cfg = _make_agent_cfg(agent_dir=str(tmp_path), soul="nicht_vorhanden.md")
        prompt = _build_system_prompt(cfg, "Test")
        assert "Test-Agent" in prompt


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
        return cfg

    async def test_unter_schwellwert_keine_aktion(self):
        sessions = self._make_sessions(estimated_tokens=1_000)
        boss_cfg = self._make_boss_cfg()
        await _compact_if_needed(sessions, "proj", boss_cfg)
        sessions.compact.assert_not_called()
        sessions.new_session.assert_not_called()

    async def test_lokales_modell_niedrigerer_schwellwert(self):
        sessions = self._make_sessions(estimated_tokens=2_500)
        boss_cfg = self._make_boss_cfg(model="llama3.2")
        # lokales Modell: threshold=2000, 2500 > 2000 → kompaktieren
        with patch("hydrahive_core.orchestrator_llm._llm_with_retry", new_callable=AsyncMock) as mock_retry:
            mock_resp = MagicMock()
            mock_resp.choices[0].message.content = "Zusammenfassung"
            mock_retry.return_value = mock_resp
            await _compact_if_needed(sessions, "proj", boss_cfg)
        sessions.compact.assert_called_once()

    async def test_zu_wenig_nachrichten_keine_kompaktierung(self):
        sessions = self._make_sessions(estimated_tokens=25_000, active_messages=3)
        boss_cfg = self._make_boss_cfg()
        await _compact_if_needed(sessions, "proj", boss_cfg)
        sessions.compact.assert_not_called()

    async def test_notfall_reset_bei_kompaktierungsfehler_und_zu_gross(self):
        sessions = self._make_sessions(estimated_tokens=35_000, active_messages=20)
        # Nach Fehler: immer noch > 30k
        sessions.estimated_tokens.side_effect = [35_000, 35_000]
        boss_cfg = self._make_boss_cfg()
        with patch("hydrahive_core.orchestrator_llm._llm_with_retry", side_effect=Exception("LLM down")):
            await _compact_if_needed(sessions, "proj", boss_cfg)
        sessions.new_session.assert_called_once_with("proj")
