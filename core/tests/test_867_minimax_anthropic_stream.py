"""#867: MiniMax via direkten Anthropic-SDK-Streaming — Unit-Tests.

Deckt:
- Client-Init: keine OAuth-Header, Bearer-Auth
- Streaming-Text-Passthrough + Usage-Akkumulation
- Native tool_use-Erkennung (KEIN XML-Fallback nötig)

Die Funktion wird noch nicht aus dem Dispatch aufgerufen — #868 macht den
Switch in stream_boss. Hier nur isolierte Funktions-Tests.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


# ----------------------------------------------------- mocks

class FakeAsyncAnthropic:
    """Minimaler Fake des anthropic.AsyncAnthropic Clients.

    captures_init wird vom Test gesetzt — nach der Instanziierung stehen die
    __init__-kwargs drin und der Test kann Header/base_url asserten.
    """
    _captured: dict = {}
    _stream_factory = None

    def __init__(self, *args, **kwargs):
        type(self)._captured = dict(kwargs)
        self.messages = MagicMock()
        self.messages.stream = type(self)._stream_factory


def _make_stream_ctx(text_chunks: list[str], final_msg):
    """Baut einen async-context-manager der client.messages.stream(...) simuliert."""

    class _Ctx:
        async def __aenter__(self_):
            return self_

        async def __aexit__(self_, exc_type, exc, tb):
            return False

        @property
        def text_stream(self_):
            async def _gen():
                for c in text_chunks:
                    yield c
            return _gen()

        async def get_final_message(self_):
            return final_msg

    def _factory(**kwargs):
        return _Ctx()

    return _factory


def _mock_final_message(
    text: str = "",
    tool_uses: list[tuple[str, str, dict]] | None = None,
    usage: dict | None = None,
):
    content = []
    if text:
        content.append(SimpleNamespace(type="text", text=text))
    for tid, name, inp in (tool_uses or []):
        content.append(SimpleNamespace(type="tool_use", id=tid, name=name, input=inp))
    return SimpleNamespace(
        content=content,
        usage=SimpleNamespace(**usage) if usage else None,
    )


def _make_boss_cfg(max_rounds: int = 3, max_tokens: int = 4096):
    return SimpleNamespace(
        id="boss-test",
        max_tool_rounds=max_rounds,
        llm=SimpleNamespace(
            max_tokens=max_tokens,
            temperature=0.7,
            model="MiniMax-M2.7",
            fallback_models=[],
            api_key_env="MINIMAX_API_KEY",
            ollama_base_url=None,
        ),
        agent_dir=None,
    )


def _make_orch_stub():
    """Minimaler orch-Stub, nur die Methoden die das Streaming anfasst."""
    orch = MagicMock()
    orch._sessions.append = AsyncMock()
    orch._write_forced_abort_handoff = AsyncMock()
    orch._allowed_tool_map = MagicMock(return_value={})
    orch._mcp_schemas_for_agent = AsyncMock(return_value=None)
    orch._reg.as_litellm_tools = MagicMock(return_value=[])
    orch._resolve_allowed_tool = MagicMock(return_value=None)
    return orch


async def _drain(gen):
    """Hilfs-Iter: sammelt alle yields aus dem async generator bis zum Ende."""
    events: list = []
    final: dict = {}
    async for ev in gen:
        if isinstance(ev, dict):
            final = ev
        else:
            events.append(ev)
    return events, final


# --------------------------------------------- client init + auth header

@pytest.mark.asyncio
async def test_minimax_stream_client_uses_bearer_not_oauth(monkeypatch):
    """Der SDK wird mit base_url=MiniMax und Authorization: Bearer-Header
    initialisiert — KEIN anthropic-beta/oauth-*-Header."""
    from hydrahive_core import orchestrator_stream as mod

    FakeAsyncAnthropic._captured = {}
    FakeAsyncAnthropic._stream_factory = _make_stream_ctx(
        text_chunks=["hallo"],
        final_msg=_mock_final_message(
            text="hallo",
            usage={"input_tokens": 10, "output_tokens": 2,
                   "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
        ),
    )
    monkeypatch.setattr("anthropic.AsyncAnthropic", FakeAsyncAnthropic)

    orch = _make_orch_stub()
    boss_cfg = _make_boss_cfg()
    usage = {"rounds": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

    await _drain(mod._stream_minimax_anthropic(
        orch, boss_cfg, "boss-test", "proj-1", "Hi",
        messages=[{"role": "user", "content": "Hi"}],
        litellm_tools=None,
        api_key="sk-mm-abc",
        model_name="MiniMax-M2.7",
        execution_mode="normal",
        _usage=usage,
    ))

    caps = FakeAsyncAnthropic._captured
    assert "minimax" in caps.get("base_url", "").lower()
    assert caps.get("api_key") == "sk-mm-abc"
    hdrs = caps.get("default_headers", {})
    assert hdrs.get("Authorization") == "Bearer sk-mm-abc"
    # KEIN OAuth-Header darf in den default_headers sein
    for k, v in hdrs.items():
        assert "oauth" not in str(k).lower() and "oauth" not in str(v).lower(), (
            f"MiniMax-Stream darf keinen OAuth-Header setzen, aber: {k}={v}"
        )


# --------------------------------------------- text + usage happy path

@pytest.mark.asyncio
async def test_minimax_stream_text_passthrough_and_usage(monkeypatch):
    """Ein Round ohne tool_use → Text wird als SSE-Event yielded, _usage wird
    korrekt gefüllt."""
    from hydrahive_core import orchestrator_stream as mod

    FakeAsyncAnthropic._captured = {}
    FakeAsyncAnthropic._stream_factory = _make_stream_ctx(
        text_chunks=["Ha", "llo", "!"],
        final_msg=_mock_final_message(
            text="Hallo!",
            usage={
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_creation_input_tokens": 80,
                "cache_read_input_tokens": 15,
            },
        ),
    )
    monkeypatch.setattr("anthropic.AsyncAnthropic", FakeAsyncAnthropic)

    orch = _make_orch_stub()
    boss_cfg = _make_boss_cfg()
    usage = {"rounds": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

    events, final = await _drain(mod._stream_minimax_anthropic(
        orch, boss_cfg, "boss-test", "proj-1", "Hi",
        messages=[{"role": "user", "content": "Hi"}],
        litellm_tools=None,
        api_key="sk-mm",
        model_name="MiniMax-M2.7",
        execution_mode="normal",
        _usage=usage,
    ))

    # Text-SSE-Events kamen an
    text_events = [e for e in events if '"text"' in e]
    assert len(text_events) == 3, f"Erwartet 3 Text-Chunks, got {len(text_events)}"

    # Usage wurde akkumuliert
    assert usage["rounds"] == 1
    assert usage["input"] == 100
    assert usage["output"] == 20
    assert usage["cache_write"] == 80
    assert usage["cache_read"] == 15

    # Finaler dict-yield enthält full_response
    assert final.get("_streamed_any") is True
    assert final.get("_full_response") == "Hallo!"


# --------------------------------------------- native tool_use detection

@pytest.mark.asyncio
async def test_minimax_stream_native_tool_use_triggers_tool_call(monkeypatch):
    """Ein nativer tool_use-Block im final_message → execute_tool_call wird
    aufgerufen. Kein XML-Fallback nötig (das wäre der #792-Hack)."""
    from hydrahive_core import orchestrator_stream as mod

    # Zwei Rounds: Round 1 emittiert tool_use, Round 2 emittiert Text (Loop endet).
    round_1_final = _mock_final_message(
        text="",
        tool_uses=[("toolu_x", "echo_tool", {"msg": "hi"})],
        usage={"input_tokens": 50, "output_tokens": 10,
               "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    )
    round_2_final = _mock_final_message(
        text="Fertig.",
        usage={"input_tokens": 60, "output_tokens": 5,
               "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    )
    rounds = [(["",], round_1_final), (["Fertig."], round_2_final)]
    call_idx = {"i": 0}

    def _factory(**kwargs):
        class _Ctx:
            async def __aenter__(self_):
                return self_

            async def __aexit__(self_, *exc):
                return False

            @property
            def text_stream(self_):
                _i = call_idx["i"]
                chunks = rounds[_i][0]

                async def _gen():
                    for c in chunks:
                        yield c
                return _gen()

            async def get_final_message(self_):
                _i = call_idx["i"]
                call_idx["i"] += 1
                return rounds[_i][1]

        return _Ctx()

    FakeAsyncAnthropic._captured = {}
    FakeAsyncAnthropic._stream_factory = _factory
    monkeypatch.setattr("anthropic.AsyncAnthropic", FakeAsyncAnthropic)

    # execute_tool_call mocken — gibt (result, is_error) zurück.
    async def fake_execute(*args, **kwargs):
        return ("tool-result-ok", False)

    monkeypatch.setattr(mod, "execute_tool_call", fake_execute)

    orch = _make_orch_stub()
    boss_cfg = _make_boss_cfg(max_rounds=3)
    usage = {"rounds": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

    events, final = await _drain(mod._stream_minimax_anthropic(
        orch, boss_cfg, "boss-test", "proj-1", "bitte echo_tool",
        messages=[{"role": "user", "content": "bitte echo_tool"}],
        litellm_tools=[{"function": {"name": "echo_tool", "description": "",
                                      "parameters": {"type": "object", "properties": {}}}}],
        api_key="sk-mm",
        model_name="MiniMax-M2.7",
        execution_mode="normal",
        _usage=usage,
    ))

    # Ein tool_call-SSE wurde emittiert (Runde 1) + Text-Events (Runde 2).
    tool_call_events = [e for e in events if '"tool_call"' in e and "echo_tool" in e]
    assert len(tool_call_events) == 1, (
        f"Native tool_use wurde nicht als tool_call-SSE dispatched — "
        f"XML-Fallback wäre zurueck. Events: {events}"
    )

    # Beide Rounds in usage akkumuliert
    assert usage["rounds"] == 2
    assert usage["input"] == 110  # 50 + 60
    assert usage["output"] == 15  # 10 + 5

    # Session-Append wurde für Tool-Call + Tool-Result aufgerufen
    assert orch._sessions.append.await_count >= 2


# --------------------------------------------- sanity: no identity in system

@pytest.mark.asyncio
async def test_minimax_stream_system_has_no_identity_wrap(monkeypatch):
    """Der gebaute system-Block darf keinen 'You are Claude Code'-Identity-
    Wrapper enthalten — das wäre der OAuth-Pfad."""
    from hydrahive_core import orchestrator_stream as mod
    from hydrahive_core.provider_config import ANTHROPIC_OAUTH_IDENTITY

    captured_kwargs: dict = {}

    def _factory(**kwargs):
        captured_kwargs.update(kwargs)

        class _Ctx:
            async def __aenter__(self_):
                return self_

            async def __aexit__(self_, *exc):
                return False

            @property
            def text_stream(self_):
                async def _gen():
                    yield "ok"
                return _gen()

            async def get_final_message(self_):
                return _mock_final_message(
                    text="ok",
                    usage={"input_tokens": 1, "output_tokens": 1,
                           "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                )

        return _Ctx()

    FakeAsyncAnthropic._captured = {}
    FakeAsyncAnthropic._stream_factory = _factory
    monkeypatch.setattr("anthropic.AsyncAnthropic", FakeAsyncAnthropic)

    orch = _make_orch_stub()
    boss_cfg = _make_boss_cfg()
    usage = {"rounds": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

    await _drain(mod._stream_minimax_anthropic(
        orch, boss_cfg, "boss-test", "proj-1", "Hi",
        messages=[{"role": "system", "content": "Du bist ein Agent."},
                  {"role": "user", "content": "Hi"}],
        litellm_tools=None,
        api_key="sk-mm",
        model_name="MiniMax-M2.7",
        execution_mode="normal",
        _usage=usage,
        static_prompt="Statischer Prompt",
    ))

    system_blocks = captured_kwargs.get("system", [])
    for block in system_blocks:
        assert ANTHROPIC_OAUTH_IDENTITY not in block.get("text", ""), (
            "MiniMax-Stream darf KEINEN 'You are Claude Code'-Identity-Block senden"
        )
