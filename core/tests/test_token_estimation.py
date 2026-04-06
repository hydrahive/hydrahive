"""Tests für token_estimation.py (#355)."""
import pytest
from hydrahive_core.token_estimation import estimate_tokens, estimate_message_tokens, estimate_messages_tokens


def test_estimate_tokens_empty():
    assert estimate_tokens("") == 1  # min 1


def test_estimate_tokens_short():
    result = estimate_tokens("Hello World")  # 11 chars
    assert 1 <= result <= 10


def test_estimate_tokens_medium():
    text = "a" * 320  # 320 chars → ~100 tokens
    result = estimate_tokens(text)
    assert 80 <= result <= 120


def test_estimate_tokens_long():
    text = "x" * 3200  # 3200 chars → ~1000 tokens
    result = estimate_tokens(text)
    assert 900 <= result <= 1100


def test_estimate_message_tokens_simple():
    msg = {"role": "user", "content": "Hello"}
    result = estimate_message_tokens(msg)
    assert result > estimate_tokens("Hello")  # overhead added


def test_estimate_message_tokens_tool():
    msg = {"role": "tool", "content": "result", "tool_call_id": "tc_123"}
    result = estimate_message_tokens(msg)
    assert result > estimate_tokens("result") + 10  # extra overhead for tool


def test_estimate_message_tokens_empty_content():
    msg = {"role": "assistant", "content": ""}
    result = estimate_message_tokens(msg)
    assert result >= 1


def test_estimate_message_tokens_none_content():
    msg = {"role": "assistant"}
    result = estimate_message_tokens(msg)
    assert result >= 1


def test_estimate_messages_tokens():
    msgs = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there, how can I help?"},
    ]
    result = estimate_messages_tokens(msgs)
    assert result > 0
    assert result == sum(estimate_message_tokens(m) for m in msgs)


def test_estimate_messages_tokens_empty():
    assert estimate_messages_tokens([]) == 0
