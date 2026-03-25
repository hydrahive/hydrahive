"""
test_orchestrator.py — Orchestrator-Unit-Tests

Testet Kernverhalten ohne echten LLM-Call:
- Context-Overflow → Session-Reset
- LLM-Fehler-Handling (Failover, generische Fehler)
- Memory-Budget-Limit im System-Prompt
- Context-Mode-Erkennung (normal vs. full)
- Token-Tracking nach LLM-Call
- Compaction-Schwellwert
- Session-Zustand nach Fehlern
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.orchestrator import Orchestrator, _should_failover
from hydrahive_core.session_manager import SessionManager, MessageRole
from hydrahive_core import tool_registry as _tool_reg_module


# ================================================================= Fixtures

def _make_agent_cfg(
    agent_id="test-boss",
    model="claude-3-5-sonnet-20241022",
    fallback_models=None,
    tools=None,
    agent_dir=None,
):
    cfg = MagicMock()
    cfg.id = agent_id
    cfg.identity = f"Test-Agent {agent_id}"
    cfg.soul = None
    cfg.agent_dir = Path(agent_dir) if agent_dir else None
    cfg.tools = tools or []
    cfg.llm = MagicMock()
    cfg.llm.model = model
    cfg.llm.fallback_models = fallback_models or []
    cfg.llm.temperature = 0.7
    cfg.llm.max_tokens = 1024
    cfg.llm.ollama_base_url = None
    cfg.max_tool_rounds = 5
    return cfg


def _make_project_cfg(boss_id="test-boss"):
    cfg = MagicMock()
    cfg.agents = MagicMock()
    cfg.agents.boss = boss_id
    cfg.agents.workers = []
    return cfg


def _make_llm_response(text="Hallo!", tool_calls=None, input_tokens=100, output_tokens=50):
    """Einfaches Mock-LLM-Response-Objekt."""
    msg = MagicMock()
    msg.content = text
    msg.tool_calls = tool_calls
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = MagicMock()
    resp.usage.input_tokens  = input_tokens
    resp.usage.output_tokens = output_tokens
    resp.usage.prompt_tokens     = 0
    resp.usage.completion_tokens = 0
    return resp


def _make_orchestrator(sessions_path):
    """Orchestrator mit gemockten Abhängigkeiten."""
    Path(sessions_path).mkdir(parents=True, exist_ok=True)
    sessions = SessionManager(str(sessions_path))
    sessions.start()

    discovery = MagicMock()
    discovery.get.return_value = _make_agent_cfg()

    runtime = MagicMock()
    orc = Orchestrator(discovery, runtime, sessions)
    return orc, sessions, discovery


# ================================================================= Context-Overflow

class TestContextOverflow:

    async def test_prompt_too_long_setzt_session_zurueck(self, tmp_path):
        """'prompt is too long' → Session wird zurückgesetzt, Nachricht für User."""
        orc, sessions, _ = _make_orchestrator(tmp_path / "s")

        with patch.object(orc, "_llm_call", side_effect=Exception(
            "Error code: 400 - prompt is too long: 201000 tokens > 200000 maximum"
        )):
            with patch.object(orc, "_build_system_prompt", return_value="S"):
                response, _ = await orc._handle_message_impl(
                    "proj-x", _make_project_cfg(), "Hallo", "user"
                )

        assert "zurückgesetzt" in response
        assert "wiederhole" in response
        assert sessions.get_context("proj-x") == []

    async def test_context_length_exceeded_wird_abgefangen(self, tmp_path):
        """'context_length_exceeded' (OpenAI-Variante) wird ebenfalls abgefangen."""
        orc, sessions, _ = _make_orchestrator(tmp_path / "s")

        with patch.object(orc, "_llm_call", side_effect=Exception("context_length_exceeded")):
            with patch.object(orc, "_build_system_prompt", return_value="S"):
                response, _ = await orc._handle_message_impl(
                    "proj-y", _make_project_cfg(), "Test", "user"
                )

        assert "zurückgesetzt" in response

    async def test_generischer_fehler_setzt_session_nicht_zurueck(self, tmp_path):
        """Normaler LLM-Fehler → kein Session-Reset, Fehler-Text zurück."""
        orc, sessions, _ = _make_orchestrator(tmp_path / "s")

        with patch.object(orc, "_llm_call", side_effect=Exception("Verbindung unterbrochen")):
            with patch.object(orc, "_build_system_prompt", return_value="S"):
                response, _ = await orc._handle_message_impl(
                    "proj-z", _make_project_cfg(), "Test", "user"
                )

        assert "zurückgesetzt" not in response
        assert "Fehler" in response or "Verbindung" in response

    async def test_kein_boss_agent_gibt_fehlermeldung(self, tmp_path):
        """Boss-Agent nicht gefunden → Fehlermeldung ohne Crash."""
        (tmp_path / "s").mkdir(parents=True, exist_ok=True)
        sessions = SessionManager(str(tmp_path / "s"))
        sessions.start()
        discovery = MagicMock()
        discovery.get.return_value = None
        orc = Orchestrator(discovery, MagicMock(), sessions)

        response, _ = await orc._handle_message_impl(
            "proj-k", _make_project_cfg("ghost"), "Test", "user"
        )
        assert "nicht gefunden" in response or "Fehler" in response


# ================================================================= LLM Failover

class TestLlmFailover:

    def test_should_failover_bei_overload(self):
        assert _should_failover(Exception("rate limit exceeded")) is True
        assert _should_failover(Exception("quota exceeded"))      is True
        assert _should_failover(Exception("overloaded"))          is True
        assert _should_failover(Exception("529"))                  is True

    def test_kein_failover_bei_auth_fehler(self):
        assert _should_failover(Exception("401 unauthorized"))  is False
        assert _should_failover(Exception("invalid_api_key"))   is False

    def test_kein_failover_bei_prompt_too_long(self):
        assert _should_failover(Exception("prompt is too long")) is False

    async def test_failover_auf_naechstes_modell(self, tmp_path):
        """Primäres Modell overloaded → automatischer Failover auf Fallback-Modell."""
        agent_cfg = _make_agent_cfg(
            model="claude-3-5-sonnet-20241022",
            fallback_models=["claude-3-haiku-20240307"],
        )
        _, sessions, discovery = _make_orchestrator(tmp_path / "s")
        discovery.get.return_value = agent_cfg
        orc = Orchestrator(discovery, MagicMock(), sessions)

        calls = []
        def fake_single(model_name, *a, **kw):
            calls.append(model_name)
            if "sonnet" in model_name:
                raise Exception("overloaded_error")
            return _make_llm_response("Fallback-Antwort")

        with patch.object(orc, "_llm_call_single", side_effect=fake_single):
            result = await orc._llm_call(agent_cfg, [{"role": "user", "content": "Hi"}], None)

        assert len(calls) == 2
        assert result.choices[0].message.content == "Fallback-Antwort"

    async def test_kein_failover_ohne_fallback_modell(self, tmp_path):
        """Wenn kein Fallback-Modell → Exception direkt propagiert."""
        agent_cfg = _make_agent_cfg(model="claude-3-5-sonnet-20241022", fallback_models=[])
        orc, sessions, _ = _make_orchestrator(tmp_path / "s")

        with patch.object(orc, "_llm_call_single", side_effect=Exception("overloaded")):
            with pytest.raises(Exception, match="overloaded"):
                await orc._llm_call(agent_cfg, [], None)


# ================================================================= Memory-Budget

class TestMemoryBudget:

    def test_context_mode_normal_fuer_standard(self):
        assert Orchestrator._context_mode("Was ist das Wetter?") == "normal"
        assert Orchestrator._context_mode("Hilf mir mit Python")  == "normal"

    def test_context_mode_full_bei_trigger(self):
        assert Orchestrator._context_mode("review mein letzter commit") == "full"
        assert Orchestrator._context_mode("zeig mir den diff zum PR")   == "full"
        assert Orchestrator._context_mode("audit dieser codebase")      == "full"
        assert Orchestrator._context_mode("analysiere alles vollständig") == "full"

    def test_memory_budget_normal_begrenzt_auf_30k(self, tmp_path):
        """Normal-Mode: Memory-Files auf 30k chars gedeckelt."""
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        (mem_dir / "big.md").write_text("x" * 50_000)

        agent_cfg = _make_agent_cfg(agent_dir=str(tmp_path))
        agent_cfg.soul = None

        orc, _, _ = _make_orchestrator(tmp_path / "s")
        prompt = orc._build_system_prompt(agent_cfg, "Normale Frage")

        assert len(prompt) < 35_000

    def test_memory_per_file_cap_fuegt_marker_ein(self, tmp_path):
        """Files über per_file_chars werden mit [gekürzt]-Marker abgeschnitten."""
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        (mem_dir / "gross.md").write_text("A" * 15_000)

        agent_cfg = _make_agent_cfg(agent_dir=str(tmp_path))
        agent_cfg.soul = None

        orc, _, _ = _make_orchestrator(tmp_path / "s")
        prompt = orc._build_system_prompt(agent_cfg, "Test")

        assert "gekürzt" in prompt or "Budget" in prompt

    def test_full_mode_laedt_mehr_als_normal(self, tmp_path):
        """full-mode erlaubt mehr Memory (150k) als normal-mode (30k)."""
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        for i in range(5):
            (mem_dir / f"mem_{i:02d}.md").write_text("B" * 20_000)

        agent_cfg = _make_agent_cfg(agent_dir=str(tmp_path))
        agent_cfg.soul = None

        orc, _, _ = _make_orchestrator(tmp_path / "s")
        normal_prompt = orc._build_system_prompt(agent_cfg, "Normale Frage")
        full_prompt   = orc._build_system_prompt(agent_cfg, "review mein letzter Code")

        assert len(full_prompt) > len(normal_prompt)

    def test_keine_memory_ohne_memory_dir(self, tmp_path):
        """Kein memory/ Verzeichnis → leerer Memory-Teil im Prompt."""
        agent_cfg = _make_agent_cfg(agent_dir=str(tmp_path))
        agent_cfg.soul = None

        orc, _, _ = _make_orchestrator(tmp_path / "s")
        prompt = orc._build_system_prompt(agent_cfg, "Test")

        assert "Persistentes Gedächtnis" not in prompt


# ================================================================= Token-Tracking

class TestTokenTracking:

    async def test_track_token_usage_wird_aufgerufen(self, tmp_path):
        """Nach erfolgreichem LLM-Call wird track_token_usage aufgerufen."""
        orc, _, _ = _make_orchestrator(tmp_path / "s")
        mock_rl = MagicMock()

        with patch.object(_tool_reg_module, "_rate_limiter", mock_rl):
            with patch.object(orc, "_llm_call", return_value=_make_llm_response("Ok", input_tokens=200, output_tokens=80)):
                with patch.object(orc, "_build_system_prompt", return_value="S"):
                    await orc._handle_message_impl("proj-t", _make_project_cfg(), "Hi", "user")

        mock_rl.track_token_usage.assert_called_once()
        _, tokens = mock_rl.track_token_usage.call_args[0]
        assert tokens == 280  # 200 + 80

    async def test_kein_tracking_bei_llm_fehler(self, tmp_path):
        """Bei LLM-Fehler kein track_token_usage."""
        orc, _, _ = _make_orchestrator(tmp_path / "s")
        mock_rl = MagicMock()

        with patch.object(_tool_reg_module, "_rate_limiter", mock_rl):
            with patch.object(orc, "_llm_call", side_effect=Exception("Timeout")):
                with patch.object(orc, "_build_system_prompt", return_value="S"):
                    await orc._handle_message_impl("proj-u", _make_project_cfg(), "Hi", "user")

        mock_rl.track_token_usage.assert_not_called()

    async def test_kein_tracking_wenn_rate_limiter_none(self, tmp_path):
        """Wenn _rate_limiter=None → kein Fehler, kein Tracking."""
        orc, _, _ = _make_orchestrator(tmp_path / "s")

        with patch.object(_tool_reg_module, "_rate_limiter", None):
            with patch.object(orc, "_llm_call", return_value=_make_llm_response("Ok")):
                with patch.object(orc, "_build_system_prompt", return_value="S"):
                    response, _ = await orc._handle_message_impl(
                        "proj-v", _make_project_cfg(), "Hi", "user"
                    )
        assert response == "Ok"


# ================================================================= Session-State

class TestSessionState:

    async def test_user_und_antwort_in_session(self, tmp_path):
        """Nach handle_message_impl sind User + Assistant in der Session."""
        orc, sessions, _ = _make_orchestrator(tmp_path / "s")

        with patch.object(orc, "_llm_call", return_value=_make_llm_response("Antwort")):
            with patch.object(orc, "_build_system_prompt", return_value="S"):
                await orc._handle_message_impl("proj-s", _make_project_cfg(), "Frage", "user")

        ctx = sessions.get_context("proj-s")
        contents = [m.get("content", "") for m in ctx]
        assert any("Frage"   in c for c in contents)
        assert any("Antwort" in c for c in contents)

    async def test_user_msg_bleibt_bei_allgemeinem_fehler(self, tmp_path):
        """Bei allgemeinem LLM-Fehler bleibt User-Nachricht in der Session (kein pop_last)."""
        orc, sessions, _ = _make_orchestrator(tmp_path / "s")

        with patch.object(orc, "_llm_call", side_effect=Exception("Timeout")):
            with patch.object(orc, "_build_system_prompt", return_value="S"):
                await orc._handle_message_impl("proj-e", _make_project_cfg(), "Test", "user")

        ctx = sessions.get_context("proj-e")
        assert any(m.get("role") == "user" and "Test" in m.get("content", "") for m in ctx)

    async def test_antwort_wird_als_assistant_gespeichert(self, tmp_path):
        """LLM-Antwort landet als assistant-Nachricht in der Session."""
        orc, sessions, _ = _make_orchestrator(tmp_path / "s")

        with patch.object(orc, "_llm_call", return_value=_make_llm_response("Meine Antwort")):
            with patch.object(orc, "_build_system_prompt", return_value="S"):
                response, _ = await orc._handle_message_impl(
                    "proj-a", _make_project_cfg(), "Frage", "user"
                )

        assert response == "Meine Antwort"
        ctx = sessions.get_context("proj-a")
        assert any(
            m.get("role") == "assistant" and "Meine Antwort" in m.get("content", "")
            for m in ctx
        )

    async def test_context_overflow_loescht_session_komplett(self, tmp_path):
        """Nach context-overflow-reset ist die Session wirklich leer."""
        orc, sessions, _ = _make_orchestrator(tmp_path / "s")

        # Vorher Nachrichten reinfüllen
        await sessions.new_session("proj-full")
        await sessions.append("proj-full", MessageRole.USER, "Alte Nachricht 1")
        await sessions.append("proj-full", MessageRole.ASSISTANT, "Alte Antwort 1")

        with patch.object(orc, "_llm_call", side_effect=Exception("prompt is too long: 201k > 200k")):
            with patch.object(orc, "_build_system_prompt", return_value="S"):
                await orc._handle_message_impl("proj-full", _make_project_cfg(), "Neue Frage", "user")

        # Session wurde zurückgesetzt — keine alten Nachrichten mehr
        ctx = sessions.get_context("proj-full")
        assert len(ctx) == 0
