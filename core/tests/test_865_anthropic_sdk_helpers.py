"""#865: Shared Anthropic-SDK helpers — Unit-Tests.

Die Helper aus orchestrator_llm.py sollen pur und testbar sein, damit #866
(MiniMax Non-Streaming) und #867 (Streaming) sie ohne Redundanz benutzen
können. Bestehendes OAuth-Verhalten wird über die Invariant-Tests 7a-7d
abgesichert (test_architecture_invariants.py).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from hydrahive_core.orchestrator_llm import (
    _apply_anthropic_history_cache_breakpoints,
    _convert_openai_tools_to_anthropic,
    _parse_anthropic_response_to_simplenamespace,
    _track_anthropic_cache_usage,
    _CACHE_FINGERPRINTS,
)
from hydrahive_core.provider_config import get_plain_system_blocks


# ---------------------------- history cache breakpoints ----------------------

def test_history_cache_breakpoints_skips_last_four_turns():
    """Die letzten 4 User/Assistant-Turns bekommen keinen Breakpoint."""
    msgs = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
        {"role": "assistant", "content": "d"},
        {"role": "user", "content": "e"},
        {"role": "assistant", "content": "f"},
    ]
    out = _apply_anthropic_history_cache_breakpoints(msgs, max_breakpoints=3)

    cached = [
        m for m in out
        if isinstance(m["content"], list)
        and any(isinstance(b, dict) and b.get("cache_control") for b in m["content"])
    ]
    assert len(cached) == 2, f"Erwartet 2 Breakpoints (alle vor den letzten 4), got {len(cached)}"
    # die letzten 4 (Indizes 2-5) dürfen NICHT gecached sein
    for m in out[2:]:
        if isinstance(m["content"], list):
            for b in m["content"]:
                if isinstance(b, dict):
                    assert "cache_control" not in b


def test_history_cache_breakpoints_respects_max_limit():
    """Max-Limit gibt die Breakpoint-Anzahl hart vor."""
    msgs = [
        {"role": "user", "content": f"m{i}"} for i in range(10)
    ]
    out = _apply_anthropic_history_cache_breakpoints(msgs, max_breakpoints=2)

    cached = sum(
        1 for m in out
        if isinstance(m["content"], list)
        and any(isinstance(b, dict) and b.get("cache_control") for b in m["content"])
    )
    assert cached == 2, f"max_breakpoints=2 → genau 2 Breakpoints, got {cached}"


def test_history_cache_breakpoints_preserves_existing_list_content():
    """Wenn content bereits eine Block-Liste ist, bleibt sie eine — nur letzter
    Block bekommt cache_control (sonst unverändert)."""
    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}, {"type": "text", "text": "bye"}]},
        *[{"role": "user", "content": f"m{i}"} for i in range(4)],
    ]
    out = _apply_anthropic_history_cache_breakpoints(msgs, max_breakpoints=3)

    first = out[0]["content"]
    assert isinstance(first, list)
    assert len(first) == 2
    assert "cache_control" not in first[0]
    assert first[1]["cache_control"] == {"type": "ephemeral"}


# ---------------------------- tool conversion -------------------------------

def test_convert_openai_tools_to_anthropic_basic():
    """OpenAI function-calling-Schema → Anthropic input_schema-Schema."""
    openai_tools = [{
        "type": "function",
        "function": {
            "name": "weather",
            "description": "Hol das Wetter",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
        },
    }]
    out = _convert_openai_tools_to_anthropic(openai_tools)
    assert out == [{
        "name": "weather",
        "description": "Hol das Wetter",
        "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
    }]


def test_convert_openai_tools_empty_returns_none():
    assert _convert_openai_tools_to_anthropic(None) is None
    assert _convert_openai_tools_to_anthropic([]) is None


def test_convert_openai_tools_fills_missing_parameters():
    """Fehlende parameters → minimaler Object-Schema-Default."""
    out = _convert_openai_tools_to_anthropic([{
        "function": {"name": "noop"}
    }])
    assert out[0]["input_schema"] == {"type": "object", "properties": {}}


# ---------------------------- response parsing ------------------------------

def _mock_anthropic_response(
    text: str = "",
    tool_uses: list[tuple[str, str, dict]] | None = None,
    stop_reason: str = "end_turn",
    usage: dict | None = None,
):
    """Baut ein Mock-Anthropic-Message-Response-Objekt mit .content + .usage."""
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


def test_parse_response_text_only():
    resp = _mock_anthropic_response(text="hallo welt", stop_reason="end_turn")
    out = _parse_anthropic_response_to_simplenamespace(resp, "test-model")
    assert out.model == "test-model"
    assert len(out.choices) == 1
    assert out.choices[0].message.content == "hallo welt"
    assert out.choices[0].message.tool_calls is None
    assert out.choices[0].finish_reason == "end_turn"


def test_parse_response_with_tool_use():
    resp = _mock_anthropic_response(
        text="let me check",
        tool_uses=[("toolu_abc", "lookup", {"q": "berlin"})],
        stop_reason="tool_use",
    )
    out = _parse_anthropic_response_to_simplenamespace(resp, "mm-model")
    tc_list = out.choices[0].message.tool_calls
    assert tc_list is not None and len(tc_list) == 1
    assert tc_list[0].id == "toolu_abc"
    assert tc_list[0].type == "function"
    assert tc_list[0].function.name == "lookup"
    assert tc_list[0].function.arguments == '{"q": "berlin"}'


def test_parse_response_usage_passthrough():
    resp = _mock_anthropic_response(
        text="ok",
        usage={
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_creation_input_tokens": 50,
            "cache_read_input_tokens": 30,
        },
    )
    out = _parse_anthropic_response_to_simplenamespace(resp, "m")
    assert out.usage.input_tokens == 100
    assert out.usage.output_tokens == 20
    assert out.usage.cache_creation_input_tokens == 50
    assert out.usage.cache_read_input_tokens == 30
    # litellm-compat-Aliase
    assert out.usage.prompt_tokens == 100
    assert out.usage.completion_tokens == 20


# ---------------------------- cache usage tracking --------------------------

def test_track_cache_usage_updates_fingerprint():
    """Nach einem Call mit cache_write ist das Fingerprint-Flag gesetzt."""
    _CACHE_FINGERPRINTS.pop("test-agent-a", None)
    resp = _mock_anthropic_response(
        text="x",
        usage={
            "input_tokens": 500,
            "output_tokens": 10,
            "cache_creation_input_tokens": 400,
            "cache_read_input_tokens": 0,
        },
    )
    cfg = SimpleNamespace(id="test-agent-a")
    _track_anthropic_cache_usage(resp, "m", cfg)
    fp = _CACHE_FINGERPRINTS["test-agent-a"]
    assert fp["had_write"] is True
    assert fp["last_write"] == 400
    assert fp["last_read"] == 0


def test_track_cache_usage_no_usage_is_noop():
    """Response ohne .usage → kein State-Update, keine Exception."""
    _CACHE_FINGERPRINTS.pop("test-agent-b", None)
    resp = SimpleNamespace(content=[], stop_reason="end_turn", usage=None)
    _track_anthropic_cache_usage(resp, "m", SimpleNamespace(id="test-agent-b"))
    assert "test-agent-b" not in _CACHE_FINGERPRINTS


# ---------------------------- plain system blocks ---------------------------

def test_plain_system_blocks_empty_prompt_returns_empty_list():
    assert get_plain_system_blocks("") == []


def test_plain_system_blocks_basic_wrap():
    """Einfacher Prompt ohne Memory-Marker bekommt cache_control."""
    out = get_plain_system_blocks("Du bist ein Agent.")
    assert out == [{
        "type": "text",
        "text": "Du bist ein Agent.",
        "cache_control": {"type": "ephemeral"},
    }]


def test_plain_system_blocks_no_identity_header():
    """#865-Kern: KEIN 'You are Claude Code'-Identity-Block (anders als OAuth)."""
    from hydrahive_core.provider_config import ANTHROPIC_OAUTH_IDENTITY
    out = get_plain_system_blocks("Agent-Prompt hier.")
    for block in out:
        assert ANTHROPIC_OAUTH_IDENTITY not in block.get("text", ""), (
            "MiniMax/Plain-System-Blocks dürfen KEINEN Identity-Wrapper enthalten"
        )


def test_plain_system_blocks_memory_split():
    """Mit Memory-Marker: statischer Teil cacheable, dynamischer Teil ohne Cache."""
    from hydrahive_core.context_channels import MEMORY_OPEN
    prompt = f"Statisch\n{MEMORY_OPEN}\nDynamisch"
    out = get_plain_system_blocks(prompt)
    assert len(out) == 2
    assert out[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in out[1]
    assert MEMORY_OPEN in out[1]["text"]
