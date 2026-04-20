"""
test_minimax_provider.py — Tests für #616 MiniMax-M2 als LLM-Provider.

Prüft:
- _resolve_model: MiniMax-Modelle werden NICHT als Ollama interpretiert und landen
  mit anthropic/-Transport + MiniMax-Endpoint.
- _provider_call_kwargs: liefert api_base + api_key nur für MiniMax-Modelle,
  respektiert api_key_env-Vorrang.
- check_llm_provider_available: OK mit MINIMAX_API_KEY, Fehlermeldung ohne Key.
- _has_minimax_provider_key: erkennt llm_config, ENV und llm.env-Datei.
- _llm_call_single: reicht api_base + api_key an litellm.acompletion.
- _compact_call: Compaction nutzt denselben MiniMax-Transport.
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
    MINIMAX_DEFAULT_BASE_URL,
    MINIMAX_DEFAULT_MEDIA_BASE_URL,
    _minimax_base_url,
    _minimax_media_base_url,
)
from hydrahive_core.router_llm import _has_minimax_provider_key


# ================================================================= _resolve_model

class TestResolveModelMinimax:

    def test_bare_name_mappt_auf_anthropic_transport(self):
        """#616 Kernfall: bare 'MiniMax-M2.7' darf NICHT als Ollama enden."""
        with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value={"providers": {}}):
            model, base = _resolve_model("MiniMax-M2.7")
        assert model == "anthropic/MiniMax-M2.7"
        assert base == MINIMAX_DEFAULT_BASE_URL

    def test_bare_name_nicht_als_ollama(self):
        """Regressions-Schutz: kein ollama_chat/-Prefix für MiniMax."""
        with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value={"providers": {}}):
            model, _ = _resolve_model("MiniMax-M2.7", ollama_base_url="http://localhost:11434")
        assert not model.startswith("ollama")
        assert model == "anthropic/MiniMax-M2.7"

    def test_minimax_prefix_form_wird_akzeptiert(self):
        """Robustheit: minimax/ Prefix-Form wird auf denselben Transport gemappt."""
        with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value={"providers": {}}):
            model, base = _resolve_model("minimax/MiniMax-M2.7")
        assert model == "anthropic/MiniMax-M2.7"
        assert base == MINIMAX_DEFAULT_BASE_URL

    def test_base_url_aus_llm_config(self):
        """Custom base_url aus providers.minimax.base_url wird genutzt."""
        fake_cfg = {"providers": {"minimax": {"base_url": "https://api.example.eu/anthropic"}}}
        with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value=fake_cfg):
            _, base = _resolve_model("MiniMax-M2.7")
        assert base == "https://api.example.eu/anthropic"


# ================================================================= _provider_call_kwargs

def _make_cfg(api_key_env: str = ""):
    cfg = MagicMock()
    cfg.llm.api_key_env = api_key_env
    return cfg


class TestProviderCallKwargs:

    def test_nicht_minimax_liefert_leeres_dict(self):
        assert _provider_call_kwargs("gpt-4o", _make_cfg()) == {}
        assert _provider_call_kwargs("claude-sonnet-4-6", _make_cfg()) == {}
        assert _provider_call_kwargs("ollama/llama3", _make_cfg()) == {}

    def test_minimax_bare_liefert_api_base_und_key_aus_env(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_API_KEY", "mm-env-secret")
        with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value={"providers": {}}):
            kw = _provider_call_kwargs("MiniMax-M2.7", _make_cfg())
        assert kw["api_base"] == MINIMAX_DEFAULT_BASE_URL
        assert kw["api_key"] == "mm-env-secret"

    def test_minimax_key_aus_llm_config(self, monkeypatch):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        fake_cfg = {"providers": {"minimax": {"api_key": "mm-config-secret"}}}
        with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value=fake_cfg):
            kw = _provider_call_kwargs("MiniMax-M2.7", _make_cfg())
        assert kw["api_key"] == "mm-config-secret"

    def test_api_key_env_hat_vorrang(self, monkeypatch):
        """agent_cfg.llm.api_key_env schlägt MINIMAX_API_KEY + config.api_key."""
        monkeypatch.setenv("MINIMAX_API_KEY", "env-default")
        monkeypatch.setenv("PROJECT_MM_KEY", "project-specific")
        fake_cfg = {"providers": {"minimax": {"api_key": "cfg-fallback"}}}
        with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value=fake_cfg):
            kw = _provider_call_kwargs("MiniMax-M2.7", _make_cfg(api_key_env="PROJECT_MM_KEY"))
        assert kw["api_key"] == "project-specific"

    def test_kein_key_liefert_api_base_ohne_key(self, monkeypatch):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value={"providers": {}}):
            kw = _provider_call_kwargs("MiniMax-M2.7", _make_cfg())
        assert "api_base" in kw
        assert "api_key" not in kw

    def test_custom_base_url_aus_config(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_API_KEY", "x")
        fake_cfg = {"providers": {"minimax": {"base_url": "https://eu.minimax.example/anthropic", "api_key": "y"}}}
        with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value=fake_cfg):
            kw = _provider_call_kwargs("MiniMax-M2.7", _make_cfg())
        assert kw["api_base"] == "https://eu.minimax.example/anthropic"


# ================================================================= check_llm_provider_available

class TestProviderAvailable:

    def test_minimax_ok_mit_env(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_API_KEY", "ok")
        with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value={"providers": {}}):
            assert check_llm_provider_available(["MiniMax-M2.7"]) is None

    def test_minimax_ok_mit_config(self, monkeypatch):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        fake_cfg = {"providers": {"minimax": {"api_key": "cfg"}}}
        with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value=fake_cfg):
            assert check_llm_provider_available(["MiniMax-M2.7"]) is None

    def test_minimax_fehlt_ohne_key(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        empty_env = tmp_path / "llm.env"
        empty_env.write_text("")
        with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value={"providers": {}}), \
             patch("hydrahive_core.orchestrator_llm.settings") as mock_settings:
            mock_settings.llm_env = empty_env
            result = check_llm_provider_available(["MiniMax-M2.7"])
        assert result is not None
        assert "LLM-Provider" in result

    def test_minimax_leeres_env_file_reicht_nicht(self, monkeypatch, tmp_path):
        """check_llm_provider_available muss konsistent zu _has_minimax_provider_key
        sein: MINIMAX_API_KEY=    in llm.env zählt NICHT als konfiguriert."""
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        env_file = tmp_path / "llm.env"
        env_file.write_text("MINIMAX_API_KEY=   \n")
        with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value={"providers": {}}), \
             patch("hydrahive_core.orchestrator_llm.settings") as mock_settings:
            mock_settings.llm_env = env_file
            result = check_llm_provider_available(["MiniMax-M2.7"])
        assert result is not None


# ================================================================= _has_minimax_provider_key

class TestHasMinimaxKey:

    def test_env_reicht(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_API_KEY", "x")
        assert _has_minimax_provider_key({}) is True

    def test_config_api_key_reicht(self, monkeypatch):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        assert _has_minimax_provider_key({"minimax": {"api_key": "x"}}) is True

    def test_enabled_ohne_key_reicht_nicht(self, monkeypatch, tmp_path):
        """Regression: enabled=True ohne api_key darf NICHT als konfiguriert gelten.
        Sonst würde /llm/available-models MiniMax anzeigen, obwohl kein Key
        vorhanden ist → nicht lauffähiger Provider wird auswählbar."""
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        empty_env = tmp_path / "llm.env"
        empty_env.write_text("")
        with patch("hydrahive_core.router_llm.settings") as mock_settings:
            mock_settings.llm_env = empty_env
            assert _has_minimax_provider_key({"minimax": {"enabled": True}}) is False

    def test_base_url_without_key_does_not_expose_model(self, monkeypatch, tmp_path):
        """Edge-Case: User speichert base_url ohne Key → MiniMax darf NICHT
        auftauchen. saveMinimaxBaseUrl() im UI hat zwar einen Client-Guard,
        aber die Wahrheit muss der Server durchsetzen."""
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        empty_env = tmp_path / "llm.env"
        empty_env.write_text("")
        with patch("hydrahive_core.router_llm.settings") as mock_settings:
            mock_settings.llm_env = empty_env
            assert _has_minimax_provider_key({
                "minimax": {
                    "enabled": True,
                    "base_url": "https://api.minimax.io/anthropic",
                    "api_key": "",
                }
            }) is False

    def test_whitespace_key_reicht_nicht(self, monkeypatch, tmp_path):
        """Regression: api_key='   ' (nur Whitespace) darf NICHT als Key gelten."""
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        empty_env = tmp_path / "llm.env"
        empty_env.write_text("")
        with patch("hydrahive_core.router_llm.settings") as mock_settings:
            mock_settings.llm_env = empty_env
            assert _has_minimax_provider_key({"minimax": {"api_key": "   "}}) is False

    def test_leer_ist_false(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        empty_env = tmp_path / "llm.env"
        empty_env.write_text("")
        with patch("hydrahive_core.router_llm.settings") as mock_settings:
            mock_settings.llm_env = empty_env
            assert _has_minimax_provider_key({}) is False

    def test_llm_env_datei_reicht(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        env_file = tmp_path / "llm.env"
        env_file.write_text("FOO=bar\nMINIMAX_API_KEY=secret\n")
        with patch("hydrahive_core.router_llm.settings") as mock_settings:
            mock_settings.llm_env = env_file
            assert _has_minimax_provider_key({}) is True

    def test_llm_env_empty_key_reicht_nicht(self, monkeypatch, tmp_path):
        """Regression: MINIMAX_API_KEY= oder MINIMAX_API_KEY=    in llm.env
        darf NICHT als konfiguriert gelten."""
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        env_file = tmp_path / "llm.env"
        env_file.write_text("MINIMAX_API_KEY=   \n")
        with patch("hydrahive_core.router_llm.settings") as mock_settings:
            mock_settings.llm_env = env_file
            assert _has_minimax_provider_key({}) is False


# ================================================================= _llm_call_single kwargs

class TestLlmCallSingleKwargs:
    """Prüft dass _llm_call_single für MiniMax api_base und api_key an
    litellm.acompletion weiterreicht — nicht implizit OPENAI_API_KEY."""

    async def test_minimax_kwargs_reichen_key_und_base_an_litellm(self, monkeypatch):
        from hydrahive_core.orchestrator_llm import _llm_call_single

        monkeypatch.setenv("MINIMAX_API_KEY", "mm-secret")
        captured: dict = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            # litellm-kompatibles Dummy-Response-Objekt
            resp = MagicMock()
            resp.usage = None
            return resp

        cfg = MagicMock()
        cfg.llm.provider = "minimax"
        cfg.llm.model = "MiniMax-M2.7"
        cfg.llm.temperature = 0.3
        cfg.llm.max_tokens = 1024
        cfg.llm.api_key_env = ""
        cfg.llm.ollama_base_url = None
        cfg.llm.thinking_budget = 0

        fake_cfg = {"providers": {"minimax": {}}, "blocked_models": []}
        with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value=fake_cfg), \
             patch("hydrahive_core.orchestrator_llm._llm_with_retry", new=lambda fn: fn()), \
             patch("hydrahive_core.orchestrator_llm.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(side_effect=fake_acompletion)
            await _llm_call_single("MiniMax-M2.7", cfg, [{"role": "user", "content": "Hi"}], None)

        assert captured.get("model") == "anthropic/MiniMax-M2.7"
        assert captured.get("api_base") == MINIMAX_DEFAULT_BASE_URL
        assert captured.get("api_key") == "mm-secret"

    async def test_minimax_litellm_call_behaelt_openai_tool_messages(self, monkeypatch):
        """MiniMax Token-Plan nutzt /anthropic, aber LiteLLM validiert Chat-Messages.

        Referenz: OpenClaw trennt Anthropic-SDK-Format (`tool_result`-Blöcke)
        von OpenAI-Chat-Format (`role: tool`). MiniMax läuft hier über LiteLLM,
        also darf kein Anthropic-`tool_result` in user.content landen.
        """
        from hydrahive_core.orchestrator_llm import _llm_call_single

        monkeypatch.setenv("MINIMAX_API_KEY", "mm-secret")
        captured: dict = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            resp = MagicMock()
            resp.usage = None
            return resp

        cfg = MagicMock()
        cfg.llm.provider = "minimax"
        cfg.llm.model = "MiniMax-M2.7"
        cfg.llm.temperature = 0.3
        cfg.llm.max_tokens = 1024
        cfg.llm.api_key_env = ""
        cfg.llm.ollama_base_url = None
        cfg.llm.thinking_budget = 0

        messages = [
            {"role": "system", "content": "sys"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "file_read", "arguments": "{\"path\":\"x\"}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "result"},
        ]

        fake_cfg = {"providers": {"minimax": {}}, "blocked_models": []}
        with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value=fake_cfg), \
             patch("hydrahive_core.orchestrator_llm._llm_with_retry", new=lambda fn: fn()), \
             patch("hydrahive_core.orchestrator_llm.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(side_effect=fake_acompletion)
            await _llm_call_single("MiniMax-M2.7", cfg, messages, None)

        assert captured.get("model") == "anthropic/MiniMax-M2.7"
        assert "system" not in captured
        assert any(m.get("role") == "tool" for m in captured["messages"])
        assert not any(
            isinstance(m.get("content"), list)
            and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in m["content"])
            for m in captured["messages"]
        )

    async def test_minimax_stream_behaelt_openai_tool_messages(self, monkeypatch):
        from types import SimpleNamespace
        from hydrahive_core.orchestrator_stream import _stream_litellm

        monkeypatch.setenv("MINIMAX_API_KEY", "mm-secret")
        captured: dict = {}

        class FakeStream:
            def __init__(self):
                self._done = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._done:
                    raise StopAsyncIteration
                self._done = True
                delta = SimpleNamespace(content="ok", tool_calls=None)
                choice = SimpleNamespace(delta=delta)
                return SimpleNamespace(choices=[choice], usage=None)

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            return FakeStream()

        cfg = MagicMock()
        cfg.max_tool_rounds = 2
        cfg.llm.provider = "minimax"
        cfg.llm.model = "MiniMax-M2.7"
        cfg.llm.temperature = 0.3
        cfg.llm.max_tokens = 1024
        cfg.llm.api_key_env = ""
        cfg.llm.ollama_base_url = None

        messages = [
            {"role": "system", "content": "sys"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "file_read", "arguments": "{\"path\":\"x\"}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "result"},
        ]

        fake_cfg = {"providers": {"minimax": {}}, "blocked_models": []}
        with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value=fake_cfg), \
             patch("litellm.acompletion", AsyncMock(side_effect=fake_acompletion)):
            chunks = []
            async for chunk in _stream_litellm(
                MagicMock(), cfg, "agent", "project", "hi",
                messages, None, "MiniMax-M2.7", None,
                {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "rounds": 0},
            ):
                chunks.append(chunk)

        assert any(isinstance(c, str) and '"ok"' in c for c in chunks)
        assert captured.get("model") == "anthropic/MiniMax-M2.7"
        assert "system" not in captured
        assert any(m.get("role") == "tool" for m in captured["messages"])
        assert not any(
            isinstance(m.get("content"), list)
            and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in m["content"])
            for m in captured["messages"]
        )


# ================================================================= _compact_call kwargs

class TestCompactCallMinimaxKwargs:
    """Regressionsschutz für Live-Befund: Compaction umging _resolve_model()
    und schickte bare MiniMax-M2.7 direkt an LiteLLM."""

    async def test_compact_call_nutzt_minimax_transport_kwargs(self, monkeypatch):
        from hydrahive_core import orchestrator_context as oc

        monkeypatch.setenv("MINIMAX_API_KEY", "mm-compact-secret")
        captured: dict = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            message = MagicMock()
            message.content = "summary"
            choice = MagicMock()
            choice.message = message
            resp = MagicMock()
            resp.choices = [choice]
            return resp

        cfg = MagicMock()
        cfg.llm.api_key_env = ""
        cfg.llm.ollama_base_url = None

        fake_cfg = {"providers": {"minimax": {}}, "blocked_models": []}
        with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value=fake_cfg), \
             patch("hydrahive_core.orchestrator_llm._llm_with_retry", new=lambda fn: fn()), \
             patch("hydrahive_core.orchestrator_context.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(side_effect=fake_acompletion)
            result = await oc._compact_call(
                cfg,
                "MiniMax-M2.7",
                [{"role": "user", "content": "summarize"}],
                128,
            )

        assert result == "summary"
        assert captured.get("model") == "anthropic/MiniMax-M2.7"
        assert captured.get("api_base") == MINIMAX_DEFAULT_BASE_URL
        assert captured.get("api_key") == "mm-compact-secret"


# ================================================================= _minimax_media_base_url

class TestMinimaxMediaBaseUrl:
    """#773 Followup: Media-Endpoint ist /v1, Chat-Endpoint ist /anthropic.

    Vor dem Fix delegierten alle drei Media-Resolver (image/video/music) an
    `_minimax_base_url()`, das seit Commit 0357e74 /anthropic liefert.
    Ergebnis: Image-Calls gingen auf /anthropic/image_generation → 404.
    """

    def test_default_ist_v1_nicht_anthropic(self):
        with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value={"providers": {}}):
            url = _minimax_media_base_url()
        assert url == MINIMAX_DEFAULT_MEDIA_BASE_URL
        assert url.endswith("/v1")
        assert "/anthropic" not in url

    def test_media_base_url_nie_identisch_mit_chat_base_url(self):
        """Kern-Invariante: Chat und Media dürfen nicht denselben Endpoint haben.
        Wenn sie identisch wären, wäre einer von beiden ein 404."""
        with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value={"providers": {}}):
            assert _minimax_media_base_url() != _minimax_base_url()

    def test_media_base_url_aus_llm_config(self):
        fake_cfg = {"providers": {"minimax": {"media_base_url": "https://eu.minimax.example/v1"}}}
        with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value=fake_cfg):
            url = _minimax_media_base_url()
        assert url == "https://eu.minimax.example/v1"

    def test_chat_base_url_override_greift_nicht_auf_media(self):
        """providers.minimax.base_url ist Chat-only — Media nimmt den Default."""
        fake_cfg = {"providers": {"minimax": {"base_url": "https://api.minimax.io/anthropic"}}}
        with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value=fake_cfg):
            assert _minimax_base_url() == "https://api.minimax.io/anthropic"
            assert _minimax_media_base_url() == MINIMAX_DEFAULT_MEDIA_BASE_URL

    def test_image_wrapper_nutzt_media_base_url(self):
        """Regression: _minimax_image_base_url() darf NICHT an _minimax_base_url
        delegieren — sonst kommt /anthropic zurück und Image-Requests → 404."""
        from hydrahive_core.minimax_image import _minimax_image_base_url
        with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value={"providers": {}}):
            url = _minimax_image_base_url()
        assert url == MINIMAX_DEFAULT_MEDIA_BASE_URL
        assert "/anthropic" not in url

    def test_video_wrapper_nutzt_media_base_url(self):
        from hydrahive_core.minimax_video import _minimax_video_base_url
        with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value={"providers": {}}):
            url = _minimax_video_base_url()
        assert url == MINIMAX_DEFAULT_MEDIA_BASE_URL
        assert "/anthropic" not in url

    def test_music_wrapper_nutzt_media_base_url(self):
        from hydrahive_core.minimax_music import _minimax_music_base_url
        with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value={"providers": {}}):
            url = _minimax_music_base_url()
        assert url == MINIMAX_DEFAULT_MEDIA_BASE_URL
        assert "/anthropic" not in url
