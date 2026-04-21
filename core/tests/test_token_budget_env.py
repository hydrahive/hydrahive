"""
test_token_budget_env.py — Token-Budget über Env-Variablen überschreibbar.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


def _logger():
    return logging.getLogger("test_token_env")


def test_default_when_env_unset(monkeypatch):
    monkeypatch.delenv("HYDRAHIVE_TOKEN_HARD_PER_HOUR", raising=False)
    monkeypatch.delenv("HYDRAHIVE_TOKEN_WARN_PER_HOUR", raising=False)
    from hydrahive_core.rate_limiter import RateLimiter, RateLimitSettings
    r = RateLimiter.from_env(_logger())
    assert r.settings.agent_token_hard_per_hour == RateLimitSettings.agent_token_hard_per_hour
    assert r.settings.agent_token_warn_per_hour == RateLimitSettings.agent_token_warn_per_hour


def test_env_override_hard(monkeypatch):
    monkeypatch.setenv("HYDRAHIVE_TOKEN_HARD_PER_HOUR", "9999999")
    from hydrahive_core.rate_limiter import RateLimiter
    r = RateLimiter.from_env(_logger())
    assert r.settings.agent_token_hard_per_hour == 9_999_999


def test_env_override_warn(monkeypatch):
    monkeypatch.setenv("HYDRAHIVE_TOKEN_WARN_PER_HOUR", "300000")
    from hydrahive_core.rate_limiter import RateLimiter
    r = RateLimiter.from_env(_logger())
    assert r.settings.agent_token_warn_per_hour == 300_000


def test_env_zero_disables_hard_limit(monkeypatch):
    monkeypatch.setenv("HYDRAHIVE_TOKEN_HARD_PER_HOUR", "0")
    from hydrahive_core.rate_limiter import RateLimiter
    r = RateLimiter.from_env(_logger())
    assert r.settings.agent_token_hard_per_hour == 0


def test_env_garbage_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("HYDRAHIVE_TOKEN_HARD_PER_HOUR", "not_a_number")
    from hydrahive_core.rate_limiter import RateLimiter, RateLimitSettings
    r = RateLimiter.from_env(_logger())
    assert r.settings.agent_token_hard_per_hour == RateLimitSettings.agent_token_hard_per_hour


def test_negative_clamped_to_zero(monkeypatch):
    monkeypatch.setenv("HYDRAHIVE_TOKEN_HARD_PER_HOUR", "-100")
    from hydrahive_core.rate_limiter import RateLimiter
    r = RateLimiter.from_env(_logger())
    assert r.settings.agent_token_hard_per_hour == 0
