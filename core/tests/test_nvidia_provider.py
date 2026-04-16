"""
test_nvidia_provider.py — Tests für #684 NVIDIA NIM als LLM-Provider.

Analog zu test_minimax_provider. Prüft:
- _resolve_model: Phase-1-Set wird auf openai/-Transport + NVIDIA-Endpoint gemappt
- _provider_call_kwargs: api_base + api_key nur für NVIDIA-Modelle aus dem Set,
  Vorrang api_key_env > NVIDIA_API_KEY > providers.nvidia.api_key
- check_llm_provider_available: OK mit NVIDIA_API_KEY oder Config-Key
- _has_nvidia_provider_key: Env / llm_env / Config
- _llm_call_single: reicht api_base + api_key an litellm.acompletion
- Strict Set-Matching: random "meta/foo" darf NICHT als NVIDIA durchgehen
"""
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.orchestrator_llm import (
    _resolve_model,
    _provider_call_kwargs,
    check_llm_provider_available,
    NVIDIA_DEFAULT_BASE_URL,
    NVIDIA_MODELS,
)
from hydrahive_core.llm_config_validation import LlmConfigValueError
from hydrahive_core.router_llm import _has_nvidia_provider_key


# ================================================================= _resolve_model

class TestResolveModelNvidia:

    def test_all_phase1_models_route_to_nvidia(self):
        """Alle 7 Phase-1-Modelle landen auf openai/-Transport + NVIDIA-Base."""
        assert len(NVIDIA_MODELS) == 7
        with patch("hydrahive_core.orchestrator_llm._load_llm_config",
                   return_value={"providers": {}}):
            for m in NVIDIA_MODELS:
                model, base = _resolve_model(m)
                assert model == f"openai/{m}", f"{m} wurde nicht als NVIDIA erkannt"
                assert base == NVIDIA_DEFAULT_BASE_URL

    def test_nvidia_model_nicht_als_ollama(self):
        """Regression: meta/llama-3.3-70b-instruct darf nicht als Ollama landen."""
        with patch("hydrahive_core.orchestrator_llm._load_llm_config",
                   return_value={"providers": {}}):
            model, _ = _resolve_model(
                "meta/llama-3.3-70b-instruct",
                ollama_base_url="http://localhost:11434",
            )
        assert not model.startswith("ollama")
        assert model == "openai/meta/llama-3.3-70b-instruct"

    def test_random_meta_prefix_ist_nicht_nvidia(self):
        """Strict Set-Matching (kein Prefix-Wildcard): meta/foo oder
        qwen/random-model müssen nicht als NVIDIA interpretiert werden —
        sonst würden zukünftige Provider-Kollisionen kaputt gehen."""
        with patch("hydrahive_core.orchestrator_llm._load_llm_config",
                   return_value={"providers": {}}):
            # Nicht-whitelisted "meta/..." → fällt auf Ollama-Fallback oder similar
            model, base = _resolve_model("meta/something-unknown")
            # darf auf jeden Fall NICHT NVIDIA-Base haben
            assert base != NVIDIA_DEFAULT_BASE_URL

    def test_base_url_aus_llm_config(self):
        """Custom base_url aus providers.nvidia.base_url wird genutzt."""
        fake_cfg = {"providers": {"nvidia": {"base_url": "https://on-prem-nim.example/v1"}}}
        with patch("hydrahive_core.orchestrator_llm._load_llm_config",
                   return_value=fake_cfg):
            _, base = _resolve_model("minimaxai/minimax-m2.7")
        assert base == "https://on-prem-nim.example/v1"


# ================================================================= _provider_call_kwargs

def _make_cfg(api_key_env: str = ""):
    cfg = MagicMock()
    cfg.llm.api_key_env = api_key_env
    return cfg


class TestProviderCallKwargsNvidia:

    def test_nicht_nvidia_liefert_leeres_dict(self):
        assert _provider_call_kwargs("gpt-4o", _make_cfg()) == {}
        assert _provider_call_kwargs("claude-sonnet-4-6", _make_cfg()) == {}
        assert _provider_call_kwargs("ollama/llama3", _make_cfg()) == {}
        # Nicht-whitelisted meta-Model
        assert _provider_call_kwargs("meta/something-unknown", _make_cfg()) == {}

    def test_nvidia_model_liefert_api_base_und_key_aus_env(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_API_KEY", "nv-env-secret")
        with patch("hydrahive_core.orchestrator_llm._load_llm_config",
                   return_value={"providers": {}}):
            kw = _provider_call_kwargs("minimaxai/minimax-m2.7", _make_cfg())
        assert kw["api_base"] == NVIDIA_DEFAULT_BASE_URL
        assert kw["api_key"] == "nv-env-secret"

    def test_nvidia_key_aus_llm_config(self, monkeypatch):
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        fake_cfg = {"providers": {"nvidia": {"api_key": "nv-config-secret"}}}
        with patch("hydrahive_core.orchestrator_llm._load_llm_config",
                   return_value=fake_cfg):
            kw = _provider_call_kwargs("meta/llama-3.3-70b-instruct", _make_cfg())
        assert kw["api_key"] == "nv-config-secret"

    def test_api_key_env_hat_vorrang(self, monkeypatch):
        """agent_cfg.llm.api_key_env schlägt NVIDIA_API_KEY + config.api_key."""
        monkeypatch.setenv("NVIDIA_API_KEY", "env-default")
        monkeypatch.setenv("PROJECT_NV_KEY", "project-specific")
        fake_cfg = {"providers": {"nvidia": {"api_key": "cfg-fallback"}}}
        with patch("hydrahive_core.orchestrator_llm._load_llm_config",
                   return_value=fake_cfg):
            kw = _provider_call_kwargs(
                "deepseek-ai/deepseek-v3.2",
                _make_cfg(api_key_env="PROJECT_NV_KEY"),
            )
        assert kw["api_key"] == "project-specific"

    def test_kein_key_liefert_api_base_ohne_key(self, monkeypatch):
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        with patch("hydrahive_core.orchestrator_llm._load_llm_config",
                   return_value={"providers": {}}):
            kw = _provider_call_kwargs("qwen/qwen3-coder-480b-a35b-instruct", _make_cfg())
        assert "api_base" in kw
        assert "api_key" not in kw

    def test_custom_base_url_aus_config(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_API_KEY", "x")
        fake_cfg = {"providers": {"nvidia": {
            "base_url": "https://on-prem-nim.example/v1",
            "api_key": "y",
        }}}
        with patch("hydrahive_core.orchestrator_llm._load_llm_config",
                   return_value=fake_cfg):
            kw = _provider_call_kwargs("moonshotai/kimi-k2-thinking", _make_cfg())
        assert kw["api_base"] == "https://on-prem-nim.example/v1"

    def test_non_ascii_config_key_wird_vor_litellm_abgewiesen(self, monkeypatch):
        """Regression: kopierte Doku-Bloecke mit Gedankenstrich duerfen nicht
        als Header an den OpenAI-Client gehen, sonst entsteht ein ascii codec
        InternalServerError."""
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        fake_cfg = {"providers": {"nvidia": {"api_key": "nvapi-invalid—text"}}}
        with patch("hydrahive_core.orchestrator_llm._load_llm_config",
                   return_value=fake_cfg), pytest.raises(LlmConfigValueError):
            _provider_call_kwargs("minimaxai/minimax-m2.7", _make_cfg())

    def test_minimax_und_nvidia_trennen_sich_sauber(self, monkeypatch):
        """Strict Namespace-Trennung: MiniMax-Direkt via "MiniMax-*" geht
        an MiniMax-Endpoint; NVIDIA-Modell "minimaxai/minimax-m2.7" geht
        an NVIDIA-Endpoint."""
        monkeypatch.setenv("MINIMAX_API_KEY", "mm-key")
        monkeypatch.setenv("NVIDIA_API_KEY", "nv-key")
        with patch("hydrahive_core.orchestrator_llm._load_llm_config",
                   return_value={"providers": {}}):
            mm = _provider_call_kwargs("MiniMax-M2.7", _make_cfg())
            nv = _provider_call_kwargs("minimaxai/minimax-m2.7", _make_cfg())
        assert mm["api_key"] == "mm-key"
        assert nv["api_key"] == "nv-key"
        assert mm["api_base"] != nv["api_base"]


# ================================================================= check_llm_provider_available

class TestProviderAvailableNvidia:

    def test_nvidia_ok_mit_env(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_API_KEY", "ok")
        with patch("hydrahive_core.orchestrator_llm._load_llm_config",
                   return_value={"providers": {}}):
            assert check_llm_provider_available(["minimaxai/minimax-m2.7"]) is None

    def test_nvidia_ok_mit_config(self, monkeypatch):
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        fake_cfg = {"providers": {"nvidia": {"api_key": "cfg"}}}
        with patch("hydrahive_core.orchestrator_llm._load_llm_config",
                   return_value=fake_cfg):
            assert check_llm_provider_available(["meta/llama-3.3-70b-instruct"]) is None

    def test_nvidia_fehlt_ohne_key(self, monkeypatch, tmp_path):
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        empty_env = tmp_path / "llm.env"
        empty_env.write_text("")
        with patch("hydrahive_core.orchestrator_llm._load_llm_config",
                   return_value={"providers": {}}), \
             patch("hydrahive_core.orchestrator_llm.settings") as mock_settings:
            mock_settings.llm_env = empty_env
            result = check_llm_provider_available(["minimaxai/minimax-m2.7"])
        assert result is not None
        assert "LLM-Provider" in result

    def test_nvidia_leeres_env_file_reicht_nicht(self, monkeypatch, tmp_path):
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        env_file = tmp_path / "llm.env"
        env_file.write_text("NVIDIA_API_KEY=   \n")
        with patch("hydrahive_core.orchestrator_llm._load_llm_config",
                   return_value={"providers": {}}), \
             patch("hydrahive_core.orchestrator_llm.settings") as mock_settings:
            mock_settings.llm_env = env_file
            result = check_llm_provider_available(["meta/llama-3.3-70b-instruct"])
        assert result is not None


# ================================================================= _has_nvidia_provider_key

class TestHasNvidiaKey:

    def test_env_reicht(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_API_KEY", "x")
        assert _has_nvidia_provider_key({}) is True

    def test_config_api_key_reicht(self, monkeypatch):
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        assert _has_nvidia_provider_key({"nvidia": {"api_key": "x"}}) is True

    def test_enabled_ohne_key_reicht_nicht(self, monkeypatch, tmp_path):
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        empty_env = tmp_path / "llm.env"
        empty_env.write_text("")
        with patch("hydrahive_core.router_llm.settings") as mock_settings:
            mock_settings.llm_env = empty_env
            assert _has_nvidia_provider_key({"nvidia": {"enabled": True}}) is False

    def test_base_url_without_key_does_not_expose_models(self, monkeypatch, tmp_path):
        """Edge-Case analog MiniMax: base_url ohne Key darf NICHT als
        konfiguriert gelten — sonst würde /llm/available-models die 7
        NVIDIA-Modelle listen ohne funktionierenden Key."""
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        empty_env = tmp_path / "llm.env"
        empty_env.write_text("")
        with patch("hydrahive_core.router_llm.settings") as mock_settings:
            mock_settings.llm_env = empty_env
            assert _has_nvidia_provider_key({
                "nvidia": {
                    "enabled": True,
                    "base_url": "https://integrate.api.nvidia.com/v1",
                    "api_key": "",
                }
            }) is False

    def test_whitespace_key_reicht_nicht(self, monkeypatch, tmp_path):
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        empty_env = tmp_path / "llm.env"
        empty_env.write_text("")
        with patch("hydrahive_core.router_llm.settings") as mock_settings:
            mock_settings.llm_env = empty_env
            assert _has_nvidia_provider_key({"nvidia": {"api_key": "   "}}) is False

    def test_leer_ist_false(self, monkeypatch, tmp_path):
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        empty_env = tmp_path / "llm.env"
        empty_env.write_text("")
        with patch("hydrahive_core.router_llm.settings") as mock_settings:
            mock_settings.llm_env = empty_env
            assert _has_nvidia_provider_key({}) is False

    def test_llm_env_datei_reicht(self, monkeypatch, tmp_path):
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        env_file = tmp_path / "llm.env"
        env_file.write_text("FOO=bar\nNVIDIA_API_KEY=secret\n")
        with patch("hydrahive_core.router_llm.settings") as mock_settings:
            mock_settings.llm_env = env_file
            assert _has_nvidia_provider_key({}) is True


# ================================================================= _llm_call_single kwargs

class TestLlmCallSingleKwargsNvidia:
    """Prüft dass _llm_call_single für NVIDIA api_base + api_key an
    litellm.acompletion weiterreicht — nicht implizit OPENAI_API_KEY."""

    async def test_nvidia_kwargs_reichen_key_und_base_an_litellm(self, monkeypatch):
        from hydrahive_core.orchestrator_llm import _llm_call_single

        monkeypatch.setenv("NVIDIA_API_KEY", "nv-secret")
        captured: dict = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            resp = MagicMock()
            resp.usage = None
            return resp

        cfg = MagicMock()
        cfg.llm.provider = "nvidia"
        cfg.llm.model = "minimaxai/minimax-m2.7"
        cfg.llm.temperature = 0.3
        cfg.llm.max_tokens = 1024
        cfg.llm.api_key_env = ""
        cfg.llm.ollama_base_url = None
        cfg.llm.thinking_budget = 0

        fake_cfg = {"providers": {"nvidia": {}}, "blocked_models": []}
        with patch("hydrahive_core.orchestrator_llm._load_llm_config",
                   return_value=fake_cfg), \
             patch("hydrahive_core.orchestrator_llm._llm_with_retry",
                   new=lambda fn: fn()), \
             patch("hydrahive_core.orchestrator_llm.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(side_effect=fake_acompletion)
            await _llm_call_single(
                "minimaxai/minimax-m2.7",
                cfg,
                [{"role": "user", "content": "Hi"}],
                None,
            )

        assert captured.get("model") == "openai/minimaxai/minimax-m2.7"
        assert captured.get("api_base") == NVIDIA_DEFAULT_BASE_URL
        assert captured.get("api_key") == "nv-secret"
