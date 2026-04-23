"""#866: MiniMax via direkten Anthropic-SDK-Call — Non-Streaming Tests.

Deckt:
- Direct-tool_use-Parsing (keine XML-Halluzinationen)
- Cache-Usage-Weitergabe (cache_read/cache_write)
- Routing: mit Feature-Flag=1 geht MiniMax durch den neuen Pfad,
  mit Flag=0 durch den bestehenden litellm-Pfad (Regression-Schutz).
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# --------------------------------------------------------------- helpers

def _make_agent_cfg(model: str = "MiniMax-M2.7", api_key_env: str = "MINIMAX_API_KEY"):
    """Minimaler Agent-Cfg-Stub wie ihn _llm_call_single erwartet."""
    return SimpleNamespace(
        id="test-agent",
        llm=SimpleNamespace(
            model=model,
            fallback_models=[],
            temperature=0.7,
            max_tokens=8192,
            thinking_budget=0,
            api_key_env=api_key_env,
            ollama_base_url=None,
        ),
        agent_dir=None,
    )


def _mock_anthropic_response(
    text: str = "",
    tool_uses: list[tuple[str, str, dict]] | None = None,
    stop_reason: str = "end_turn",
    usage: dict | None = None,
):
    blocks = []
    if text:
        blocks.append(SimpleNamespace(type="text", text=text))
    for tool_id, name, inp in (tool_uses or []):
        blocks.append(SimpleNamespace(type="tool_use", id=tool_id, name=name, input=inp))
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop_reason,
        usage=SimpleNamespace(**usage) if usage else None,
    )


def _mock_raw_response(parsed, headers=None):
    raw = MagicMock()
    raw.parse = MagicMock(return_value=parsed)
    raw.headers = headers or {}
    return raw


# ------------------------------------------------------ direct tool_use

@pytest.mark.asyncio
async def test_minimax_anthropic_direct_tool_use(monkeypatch):
    """Native tool_use-Blöcke aus MiniMax-Response werden in SimpleNamespace-
    tool_calls konvertiert — KEIN XML-Parsing-Fallback nötig."""
    from hydrahive_core import orchestrator_llm as mod

    # Mock Anthropic client
    mock_resp = _mock_anthropic_response(
        text="Ich checke das.",
        tool_uses=[("toolu_123", "file_read", {"path": "/etc/foo"})],
        stop_reason="tool_use",
        usage={
            "input_tokens": 500,
            "output_tokens": 30,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    )
    raw = _mock_raw_response(mock_resp)
    mock_client = MagicMock()
    mock_client.messages.with_raw_response.create = AsyncMock(return_value=raw)

    class FakeAsyncAnthropic:
        def __init__(self, *a, **k):
            self._init_kwargs = k
        def __new__(cls, *a, **k):
            inst = super().__new__(cls)
            inst._init_kwargs = k
            inst.messages = mock_client.messages
            return inst

    monkeypatch.setattr("anthropic.AsyncAnthropic", FakeAsyncAnthropic)

    cfg = _make_agent_cfg()
    out = await mod._minimax_anthropic_call(
        cfg,
        messages=[{"role": "user", "content": "lies /etc/foo"}],
        tools=[{"function": {"name": "file_read", "description": "Lies eine Datei",
                              "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}],
        api_key="sk-test-12345",
        model_name="MiniMax-M2.7",
    )

    assert out.model == "MiniMax-M2.7"
    tc_list = out.choices[0].message.tool_calls
    assert tc_list is not None and len(tc_list) == 1, (
        "Native tool_use wurde nicht geparst — XML-Halluzinations-Fallback wäre zurück"
    )
    assert tc_list[0].id == "toolu_123"
    assert tc_list[0].function.name == "file_read"
    assert json.loads(tc_list[0].function.arguments) == {"path": "/etc/foo"}


# ---------------------------------------------------- cache usage

@pytest.mark.asyncio
async def test_minimax_anthropic_cache_usage(monkeypatch):
    """cache_read_input_tokens + cache_creation_input_tokens werden in out.usage
    weitergereicht — identisch zum OAuth-Pfad."""
    from hydrahive_core import orchestrator_llm as mod

    mock_resp = _mock_anthropic_response(
        text="ok",
        usage={
            "input_tokens": 1000,
            "output_tokens": 50,
            "cache_creation_input_tokens": 800,
            "cache_read_input_tokens": 150,
        },
    )
    raw = _mock_raw_response(mock_resp)
    mock_client = MagicMock()
    mock_client.messages.with_raw_response.create = AsyncMock(return_value=raw)

    class FakeAsyncAnthropic:
        def __new__(cls, *a, **k):
            inst = super().__new__(cls)
            inst.messages = mock_client.messages
            return inst

    monkeypatch.setattr("anthropic.AsyncAnthropic", FakeAsyncAnthropic)

    cfg = _make_agent_cfg()
    out = await mod._minimax_anthropic_call(
        cfg,
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        api_key="sk-test",
        model_name="MiniMax-M2.7",
    )

    assert out.usage.input_tokens == 1000
    assert out.usage.output_tokens == 50
    assert out.usage.cache_creation_input_tokens == 800
    assert out.usage.cache_read_input_tokens == 150


# -------------------------------------------- bearer-header + no-identity

@pytest.mark.asyncio
async def test_minimax_anthropic_uses_bearer_auth_not_oauth(monkeypatch):
    """Der SDK wird mit base_url=MiniMax + Authorization-Bearer initialisiert —
    KEINE anthropic-beta/oauth-*-Header (das wäre der OAuth-Pfad)."""
    from hydrahive_core import orchestrator_llm as mod

    captured: dict = {}

    class FakeAsyncAnthropic:
        def __init__(self, *a, **k):
            captured.update(k)
            self.messages = MagicMock()
            resp = _mock_anthropic_response(text="ok", usage={
                "input_tokens": 10, "output_tokens": 2,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            })
            raw = _mock_raw_response(resp)
            self.messages.with_raw_response.create = AsyncMock(return_value=raw)

    monkeypatch.setattr("anthropic.AsyncAnthropic", FakeAsyncAnthropic)

    cfg = _make_agent_cfg()
    await mod._minimax_anthropic_call(
        cfg,
        messages=[{"role": "user", "content": "x"}],
        tools=None,
        api_key="sk-minimax-abc",
        model_name="MiniMax-M2.7",
    )

    # base_url ist MiniMax-spezifisch
    assert "minimax" in captured.get("base_url", "").lower()
    # Authorization-Header ist Bearer
    hdrs = captured.get("default_headers", {})
    assert hdrs.get("Authorization") == "Bearer sk-minimax-abc"
    # KEIN anthropic-beta / oauth-2025-04-20 (das wäre der OAuth-Pfad)
    for k, v in hdrs.items():
        assert "oauth" not in k.lower() and "oauth" not in str(v).lower(), (
            f"MiniMax-Call darf KEINEN OAuth-Header haben, aber: {k}={v}"
        )


# ---------------------------------------------------- routing with flag

@pytest.mark.asyncio
async def test_minimax_routes_to_anthropic_sdk_when_flag_on(monkeypatch):
    """Mit HYDRAHIVE_MINIMAX_ANTHROPIC_SDK=1 und MiniMax-Model + Key wird
    _minimax_anthropic_call aufgerufen, litellm NICHT."""
    from hydrahive_core import orchestrator_llm as mod

    monkeypatch.setenv("HYDRAHIVE_MINIMAX_ANTHROPIC_SDK", "1")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-mm-test")

    called = {"minimax_anthropic": 0, "litellm": 0}

    async def fake_minimax_anthropic(*a, **k):
        called["minimax_anthropic"] += 1
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=None), finish_reason="end_turn")], model="MiniMax-M2.7", usage=None)

    async def fake_litellm_acompletion(*a, **k):
        called["litellm"] += 1
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=None), finish_reason="end_turn")], model="MiniMax-M2.7", usage=None)

    monkeypatch.setattr(mod, "_minimax_anthropic_call", fake_minimax_anthropic)
    monkeypatch.setattr("litellm.acompletion", fake_litellm_acompletion)

    cfg = _make_agent_cfg(model="MiniMax-M2.7")
    await mod._llm_call_single("MiniMax-M2.7", cfg, [{"role": "user", "content": "hi"}], tools=None)

    assert called["minimax_anthropic"] == 1, "Neuer Pfad hätte aufgerufen werden müssen"
    assert called["litellm"] == 0, "litellm darf bei Flag=1 + MiniMax nicht ran"


@pytest.mark.asyncio
async def test_minimax_routes_to_litellm_when_flag_off(monkeypatch):
    """Mit Flag=0 (Default) bleibt MiniMax auf dem litellm-Pfad — Regressions-Schutz."""
    from hydrahive_core import orchestrator_llm as mod

    monkeypatch.delenv("HYDRAHIVE_MINIMAX_ANTHROPIC_SDK", raising=False)
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-mm-test")

    called = {"minimax_anthropic": 0, "litellm": 0}

    async def fake_minimax_anthropic(*a, **k):
        called["minimax_anthropic"] += 1
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=None), finish_reason="end_turn")], model="x", usage=None)

    async def fake_litellm_acompletion(*a, **k):
        called["litellm"] += 1
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=None), finish_reason="end_turn")], model="x", usage=None)

    monkeypatch.setattr(mod, "_minimax_anthropic_call", fake_minimax_anthropic)
    monkeypatch.setattr("litellm.acompletion", fake_litellm_acompletion)

    cfg = _make_agent_cfg(model="MiniMax-M2.7")
    await mod._llm_call_single("MiniMax-M2.7", cfg, [{"role": "user", "content": "hi"}], tools=None)

    assert called["minimax_anthropic"] == 0, "Neuer Pfad darf bei Flag=0 nicht aktiv sein"
    assert called["litellm"] == 1, "litellm bleibt Default-Pfad für MiniMax"


# ---------------------------------------------------- flag helper

def test_flag_helper_truthy_values(monkeypatch):
    from hydrahive_core.orchestrator_llm import _minimax_anthropic_sdk_enabled

    for v in ("1", "true", "TRUE", "yes", "on", " 1 "):
        monkeypatch.setenv("HYDRAHIVE_MINIMAX_ANTHROPIC_SDK", v)
        assert _minimax_anthropic_sdk_enabled() is True, f"Sollte truthy für '{v}'"


def test_flag_helper_falsy_values(monkeypatch):
    from hydrahive_core.orchestrator_llm import _minimax_anthropic_sdk_enabled

    monkeypatch.delenv("HYDRAHIVE_MINIMAX_ANTHROPIC_SDK", raising=False)
    assert _minimax_anthropic_sdk_enabled() is False

    for v in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("HYDRAHIVE_MINIMAX_ANTHROPIC_SDK", v)
        assert _minimax_anthropic_sdk_enabled() is False, f"Sollte falsy für '{v}'"
