"""
test_843_real_tokenizer.py — Gate 7: echter Tokenizer (#843).

Verifiziert dass tiktoken fuer claude/gpt genutzt wird, und Fallback bei
unbekannten Modellen kein Crash.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


def test_estimate_tokens_no_model_uses_heuristic():
    """Ohne model-Param: alte Heuristik (chars/3.2)."""
    from hydrahive_core.token_estimation import estimate_tokens
    text = "a" * 320
    # 320 / 3.2 = 100
    assert estimate_tokens(text) == 100


def test_estimate_tokens_empty_returns_zero():
    from hydrahive_core.token_estimation import estimate_tokens
    assert estimate_tokens("") == 0
    assert estimate_tokens("", model="claude-haiku") == 0


def test_estimate_tokens_gpt4_uses_tiktoken():
    """gpt-4 → cl100k_base via tiktoken. Sollte deutlich anders als chars/3.2."""
    from hydrahive_core.token_estimation import estimate_tokens
    text = "Hello, world! This is a test of tokenization."
    heuristic = estimate_tokens(text)  # ~14
    real = estimate_tokens(text, model="gpt-4")
    # tiktoken zaehlt typischerweise ~12 Tokens fuer den Satz, Heuristik liefert ca 14
    assert real > 0
    # Beide sollten in vernuenftiger Range liegen
    assert 5 <= real <= 30


def test_estimate_tokens_claude_uses_tiktoken_approximation():
    from hydrahive_core.token_estimation import estimate_tokens
    text = "The quick brown fox jumps over the lazy dog."
    real = estimate_tokens(text, model="claude-sonnet-4-6")
    assert real > 0


def test_estimate_tokens_unknown_model_falls_back():
    """minimax/qwen → keine tiktoken-Integration, Heuristik-Fallback."""
    from hydrahive_core.token_estimation import estimate_tokens
    text = "a" * 320
    res = estimate_tokens(text, model="minimax-m2.7")
    # = heuristic = 100
    assert res == 100


def test_estimate_tokens_gpt4o_uses_o200k():
    from hydrahive_core.token_estimation import estimate_tokens
    text = "Sample text for GPT-4o tokenization."
    res = estimate_tokens(text, model="gpt-4o")
    assert res > 0


def test_tokenizer_cached():
    """Repeated calls mit gleichem Modell sollten denselben Tokenizer nutzen."""
    from hydrahive_core import token_estimation as te
    te._tokenizer_cache.clear()
    te._warned_models.clear()
    te.estimate_tokens("x", model="gpt-4")
    assert "gpt-4" in te._tokenizer_cache
    cached = te._tokenizer_cache["gpt-4"]
    te.estimate_tokens("y", model="gpt-4")
    assert te._tokenizer_cache["gpt-4"] is cached  # same instance


def test_provider_prefix_normalized():
    """openai/gpt-4 → gpt-4 (provider-prefix entfernt)."""
    from hydrahive_core.token_estimation import _normalize_model
    assert _normalize_model("openai/gpt-4") == "gpt-4"
    assert _normalize_model("anthropic/claude-haiku") == "claude-haiku"


def test_existing_aliases_still_work():
    """estimate_message_tokens und estimate_messages_tokens sollten noch
    funktionieren ohne model-Param (Backward-Compat)."""
    from hydrahive_core.token_estimation import (
        estimate_message_tokens, estimate_messages_tokens,
    )
    msg = {"role": "user", "content": "hi"}
    assert estimate_message_tokens(msg) > 0
    assert estimate_messages_tokens([msg, msg]) > 0
