"""Test #778: Token-Budget Pre-Check mit Call-Groessen-Schaetzung."""
from __future__ import annotations

import pytest

from hydrahive_core.rate_limiter import RateLimiter, TokenBudgetExceeded, RateLimitSettings
from hydrahive_core.token_estimation import estimate_call_tokens


def _mk(hard: int = 100_000) -> RateLimiter:
    return RateLimiter(settings=RateLimitSettings(
        agent_token_hard_per_hour=hard,
        agent_token_warn_per_hour=0,
    ))


# ── Pre-Check mit Schaetzung ─────────────────────────────────────────────────

def test_precheck_blocks_when_projected_over_hard():
    rl = _mk()
    rl.track_token_usage("a1", 50_000)
    with pytest.raises(TokenBudgetExceeded) as exc:
        rl.check_token_budget("a1", estimated_next_call_tokens=60_000)
    # projected = 110k, hard = 100k
    assert exc.value.tokens_used == 110_000


def test_precheck_allows_when_projected_within_hard():
    rl = _mk()
    rl.track_token_usage("a1", 50_000)
    rl.check_token_budget("a1", estimated_next_call_tokens=40_000)  # no raise


def test_precheck_backwards_compat_zero_estimate_over_limit():
    """Default estimated=0 erhaelt altes Verhalten exakt."""
    rl = _mk()
    # track raise selbst bei 110k, weil track_token_usage ebenso enforced.
    with pytest.raises(TokenBudgetExceeded):
        rl.track_token_usage("a1", 110_000)
    # Nach dem raise ist der Eintrag trotzdem gespeichert — get_token_usage_hour
    # zeigt den Verbrauch.
    with pytest.raises(TokenBudgetExceeded):
        rl.check_token_budget("a1")  # no estimate, pure history


def test_precheck_zero_estimate_under_limit_ok():
    rl = _mk()
    rl.track_token_usage("a1", 50_000)
    rl.check_token_budget("a1")  # 50k < 100k, no raise


def test_precheck_negative_estimate_clamped_to_zero():
    """Defensive: negative Werte werden auf 0 geclampt."""
    rl = _mk()
    rl.track_token_usage("a1", 90_000)
    # estimated=-1000 → max(0, -1000) = 0 → 90k < 100k → ok
    rl.check_token_budget("a1", estimated_next_call_tokens=-1000)


def test_precheck_disabled_when_hard_zero():
    """hard=0 → Pre-Check deaktiviert, kein raise selbst bei Mega-Wert."""
    rl = _mk(hard=0)
    rl.track_token_usage("a1", 5_000_000)
    rl.check_token_budget("a1", estimated_next_call_tokens=5_000_000)


# ── estimate_call_tokens ─────────────────────────────────────────────────────

def test_estimate_text_content():
    msgs = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello"},
    ]
    t = estimate_call_tokens(msgs, tools=None)
    assert t > 0
    # Kurzer Text, Schaetzung sollte unter 50 bleiben
    assert t < 100


def test_estimate_tools_adds_tokens():
    msgs = [{"role": "user", "content": "hi"}]
    no_tools = estimate_call_tokens(msgs, tools=None)
    with_tools = estimate_call_tokens(msgs, tools=[
        {"type": "function", "function": {
            "name": "shell_exec", "description": "run a shell command",
            "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
        }},
    ])
    assert with_tools > no_tools


def test_estimate_image_block_pauschal():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "what is this?"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAA"}},
    ]}]
    t = estimate_call_tokens(msgs, tools=None)
    assert t >= 1000  # pauschal fuer das image


def test_estimate_openai_image_url_block():
    """OpenAI-Variante image_url wird auch als 1000 gezaehlt."""
    msgs = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]}]
    t = estimate_call_tokens(msgs, tools=None)
    assert t >= 1000


def test_estimate_empty_messages():
    assert estimate_call_tokens([], tools=None) == 0


# ── Integration: Pre-Check + Schaetzung ──────────────────────────────────────

def test_precheck_with_computed_estimate():
    rl = _mk(hard=500)  # sehr kleines Limit
    rl.track_token_usage("a1", 0)
    msgs = [
        {"role": "system", "content": "x" * 2000},  # ~625 tokens
    ]
    estimate = estimate_call_tokens(msgs, tools=None)
    # estimate > hard (625 > 500) → raise
    with pytest.raises(TokenBudgetExceeded):
        rl.check_token_budget("a1", estimated_next_call_tokens=estimate)
