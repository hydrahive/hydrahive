"""
test_orchestrator_llm.py — Tests für orchestrator_llm.py

Testet standalone-Funktionen ohne echten LLM-Call:
- _should_failover: Failover-Erkennung
- _resolve_model: Model-Name-Normalisierung
- _llm_with_retry: Retry-Logik + Backoff
- _llm_call/_llm_call_single: Failover-Chain
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.orchestrator_llm import (
    _should_failover,
    _resolve_model,
    _llm_with_retry,
    _llm_call,
    _llm_call_single,
)


# ================================================================= _should_failover

class TestShouldFailover:

    def test_rate_limit_triggers_failover(self):
        assert _should_failover(Exception("rate limit exceeded")) is True
        assert _should_failover(Exception("429 too many requests")) is True

    def test_quota_triggers_failover(self):
        assert _should_failover(Exception("quota exceeded")) is True
        assert _should_failover(Exception("exceeded your current quota")) is True

    def test_overloaded_triggers_failover(self):
        assert _should_failover(Exception("overloaded")) is True
        assert _should_failover(Exception("529 overloaded_error")) is True

    def test_credit_triggers_failover(self):
        assert _should_failover(Exception("insufficient credit")) is True
        assert _should_failover(Exception("your credit balance is too low")) is True

    def test_auth_error_failover(self):
        """Auth-Fehler lösen Failover aus (abgelaufene Tokens → nächstes Modell)."""
        assert _should_failover(Exception("401 unauthorized")) is True
        assert _should_failover(Exception("OAuth token has expired")) is True
        assert _should_failover(Exception("invalid api key")) is True

    def test_context_overflow_kein_failover(self):
        assert _should_failover(Exception("prompt is too long")) is False
        assert _should_failover(Exception("context_length_exceeded")) is False

    def test_verbindungsfehler_kein_failover(self):
        assert _should_failover(Exception("connection refused")) is False
        assert _should_failover(Exception("timeout"))            is False


# ================================================================= _resolve_model

class TestResolveModel:

    def test_claude_bekommt_anthropic_prefix(self):
        model, base = _resolve_model("claude-3-5-sonnet-20241022")
        assert model == "anthropic/claude-3-5-sonnet-20241022"
        assert base is None

    def test_gpt_bekommt_openai_prefix(self):
        model, base = _resolve_model("gpt-4o")
        assert model == "openai/gpt-4o"
        assert base is None

    def test_o1_bekommt_openai_prefix(self):
        model, base = _resolve_model("o1-mini")
        assert model == "openai/o1-mini"
        assert base is None

    def test_ollama_slash_wird_zu_ollama_chat(self):
        # ollama_base_url explizit setzen um WKS-Lookup zu umgehen
        model, base = _resolve_model("ollama/llama3", ollama_base_url="http://localhost:11434")
        assert model == "ollama_chat/llama3"
        assert base == "http://localhost:11434"

    def test_unbekannter_name_wird_ollama_chat(self):
        model, base = _resolve_model("llama3.2", ollama_base_url="http://localhost:11434")
        assert model == "ollama_chat/llama3.2"
        assert base == "http://localhost:11434"

    def test_provider_prefix_bleibt_unveraendert(self):
        model, base = _resolve_model("anthropic/claude-3-haiku-20240307")
        assert model == "anthropic/claude-3-haiku-20240307"
        assert base is None

    def test_ollama_base_url_override(self):
        model, base = _resolve_model("llama3", ollama_base_url="http://192.168.1.5:11434")
        assert base == "http://192.168.1.5:11434"

    def test_ollama_slash_mit_custom_base_url(self):
        model, base = _resolve_model("ollama/mistral", ollama_base_url="http://wks:11434")
        assert model == "ollama_chat/mistral"
        assert base == "http://wks:11434"


# ================================================================= _llm_with_retry

class TestLlmWithRetry:

    async def test_erfolg_beim_ersten_versuch(self):
        mock = AsyncMock(return_value="ok")
        result = await _llm_with_retry(mock)
        assert result == "ok"
        assert mock.call_count == 1

    async def test_kein_retry_bei_auth_fehler(self):
        mock = AsyncMock(side_effect=Exception("401 unauthorized"))
        with pytest.raises(Exception, match="401"):
            await _llm_with_retry(mock)
        assert mock.call_count == 1  # kein Retry

    async def test_kein_retry_bei_quota_fehler(self):
        # #423: Quota/Billing → kein Retry. Rate-Limit → wird jetzt retried.
        mock = AsyncMock(side_effect=Exception("quota exceeded"))
        with pytest.raises(Exception, match="quota"):
            await _llm_with_retry(mock)
        assert mock.call_count == 1

    async def test_rate_limit_wird_retried(self):
        # #423: 429/rate_limit → retry mit Backoff
        mock = AsyncMock(side_effect=Exception("rate_limit exceeded"))
        with pytest.raises(Exception, match="rate_limit"):
            await _llm_with_retry(mock, max_attempts=2, base_delay=0.01)
        assert mock.call_count == 2  # 1 initial + 1 retry

    async def test_retry_bei_allgemeinem_fehler(self):
        # 2x scheitern, 3. Versuch erfolgreich
        calls = 0
        async def factory():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise Exception("server error 503")
            return "ok"

        with patch("asyncio.sleep"):  # Backoff überspringen
            result = await _llm_with_retry(factory, max_attempts=3, base_delay=0.0)
        assert result == "ok"
        assert calls == 3

    async def test_nach_max_attempts_exception_propagiert(self):
        mock = AsyncMock(side_effect=Exception("flaky"))
        with patch("asyncio.sleep"):
            with pytest.raises(Exception, match="flaky"):
                await _llm_with_retry(mock, max_attempts=2, base_delay=0.0)
        assert mock.call_count == 2


# ================================================================= _llm_call Failover

class TestLlmCallFailover:

    def _make_agent_cfg(self, model="claude-3-5-sonnet-20241022", fallback_models=None):
        cfg = MagicMock()
        cfg.llm.model = model
        cfg.llm.fallback_models = fallback_models or []
        cfg.llm.temperature = 0.7
        cfg.llm.max_tokens = 1024
        cfg.llm.ollama_base_url = None
        return cfg

    async def test_failover_auf_naechstes_modell(self):
        agent_cfg = self._make_agent_cfg(
            model="claude-3-5-sonnet-20241022",
            fallback_models=["claude-3-haiku-20240307"],
        )
        calls = []

        async def fake_single(model_name, *a, **kw):
            calls.append(model_name)
            if "sonnet" in model_name:
                raise Exception("overloaded_error")
            resp = MagicMock()
            resp.choices[0].message.content = "Fallback-Antwort"
            return resp

        with patch("hydrahive_core.orchestrator_llm._llm_call_single", side_effect=fake_single):
            result = await _llm_call(agent_cfg, [{"role": "user", "content": "Hi"}], None)

        assert len(calls) == 2
        assert result.choices[0].message.content == "Fallback-Antwort"

    async def test_kein_failover_ohne_fallback(self):
        agent_cfg = self._make_agent_cfg(model="claude-3-5-sonnet-20241022", fallback_models=[])

        async def fake_single(model_name, *a, **kw):
            raise Exception("overloaded")

        with patch("hydrahive_core.orchestrator_llm._llm_call_single", side_effect=fake_single):
            with pytest.raises(Exception, match="overloaded"):
                await _llm_call(agent_cfg, [], None)

    async def test_failover_bei_auth_fehler(self):
        """Auth-Fehler (401/expired) lösen jetzt Failover auf nächstes Modell aus."""
        agent_cfg = self._make_agent_cfg(
            model="claude-3-5-sonnet-20241022",
            fallback_models=["claude-3-haiku-20240307"],
        )
        calls = []

        async def fake_single(model_name, *a, **kw):
            calls.append(model_name)
            if model_name == "claude-3-5-sonnet-20241022":
                raise Exception("401 OAuth token has expired")
            return MagicMock(choices=[MagicMock(message=MagicMock(content="ok", tool_calls=None))],
                            usage=MagicMock(prompt_tokens=10, completion_tokens=5))

        with patch("hydrahive_core.orchestrator_llm._llm_call_single", side_effect=fake_single):
            result = await _llm_call(agent_cfg, [], None)

        # Failover auf Fallback-Modell
        assert len(calls) == 2
        assert calls[1] == "claude-3-haiku-20240307"
