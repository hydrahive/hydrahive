"""
test_820_project_token_budget.py — Pro-Projekt Token-Budget Override.

Stellt sicher dass token_budget in der ProjectConfig die globale
RateLimitSettings-Schwelle pro check_token_budget/track_token_usage
Aufruf überschreibt.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


# ─── Datenmodell ────────────────────────────────────────────────────────

def test_project_config_token_budget_default():
    """Wenn token_budget nicht gesetzt → beide Felder None."""
    from hydrahive_core.project_config import ProjectConfig, ProjectIdentity
    cfg = ProjectConfig(id="x", identity=ProjectIdentity(name="X"))
    assert cfg.token_budget.hard_per_hour is None
    assert cfg.token_budget.warn_per_hour is None


def test_project_config_token_budget_override():
    from hydrahive_core.project_config import ProjectConfig, ProjectIdentity, TokenBudget
    cfg = ProjectConfig(
        id="y", identity=ProjectIdentity(name="Y"),
        token_budget=TokenBudget(hard_per_hour=5_000_000, warn_per_hour=1_000_000),
    )
    assert cfg.token_budget.hard_per_hour == 5_000_000
    assert cfg.token_budget.warn_per_hour == 1_000_000


def test_project_config_token_budget_zero_disabled():
    from hydrahive_core.project_config import ProjectConfig, ProjectIdentity
    cfg = ProjectConfig(
        id="z", identity=ProjectIdentity(name="Z"),
        token_budget={"hard_per_hour": 0},  # dict-Form unterstützt
    )
    assert cfg.token_budget.hard_per_hour == 0
    assert cfg.token_budget.warn_per_hour is None


# ─── RateLimiter Override-Logik ────────────────────────────────────────

@pytest.fixture
def limiter():
    from hydrahive_core.rate_limiter import RateLimiter, RateLimitSettings
    rl = RateLimiter(settings=RateLimitSettings(
        agent_token_warn_per_hour=500_000,
        agent_token_hard_per_hour=1_000_000,
    ))
    return rl


def test_resolve_thresholds_global_default(limiter):
    warn, hard = limiter._resolve_thresholds()
    assert warn == 500_000
    assert hard == 1_000_000


def test_resolve_thresholds_project_override(limiter):
    warn, hard = limiter._resolve_thresholds(warn_override=200_000, hard_override=400_000)
    assert warn == 200_000
    assert hard == 400_000


def test_resolve_thresholds_project_zero_disables(limiter):
    """Project setzt 0 → disabled (überschreibt globalen Default)."""
    _, hard = limiter._resolve_thresholds(hard_override=0)
    assert hard == 0


def test_resolve_thresholds_project_negative_clamped(limiter):
    """Negative Werte werden auf 0 geklemmt (defensiv)."""
    _, hard = limiter._resolve_thresholds(hard_override=-100)
    assert hard == 0


def test_check_token_budget_uses_override_lower(limiter):
    """Project-Override liegt unter globalem → schlägt früher zu."""
    from hydrahive_core.rate_limiter import TokenBudgetExceeded
    # Pre-fill bucket über project-override aber unter global
    limiter.track_token_usage("agent-x", 600_000)
    # Globaler Hard ist 1M → durchgehen
    limiter.check_token_budget("agent-x")
    # Project-Override 500k → blocken
    with pytest.raises(TokenBudgetExceeded):
        limiter.check_token_budget("agent-x", hard_override=500_000)


def test_check_token_budget_override_zero_disables(limiter):
    """Auch wenn Bucket voll: Project=0 → kein Block."""
    # Bucket ohne raise befüllen (track wirft sonst beim Setup); wir hängen
    # die Einträge direkt rein, damit der Test nur check_token_budget testet.
    import time as _t
    limiter._agent_token_usage["agent-y"].append((_t.time(), 999_999_999))
    # Mit Override=0 → disabled, kein Throw
    limiter.check_token_budget("agent-y", hard_override=0)


def test_track_token_usage_override_warn(limiter, caplog):
    import logging as _logging
    caplog.set_level(_logging.WARNING)
    # Kein Warn auf globalem Niveau (500k), aber Override 100k → Warn jetzt
    limiter.track_token_usage("agent-z", 200_000, warn_override=100_000)
    # Erwartet: Warn-Log mit "Token-Budget-Warnung"
    assert any("Token-Budget-Warnung" in r.message for r in caplog.records), \
        "Warn-Log mit Override fehlt"


def test_track_token_usage_override_hard_raises(limiter):
    from hydrahive_core.rate_limiter import TokenBudgetExceeded
    with pytest.raises(TokenBudgetExceeded):
        limiter.track_token_usage("agent-q", 500_000, hard_override=100_000)


def test_track_token_usage_default_when_override_none(limiter):
    """None-Override → globaler Default. 500k bei warn=500k → kein Warn."""
    # Global hard 1M, warn 500k. Track 400k → kein Warn, kein Hard.
    limiter.track_token_usage("agent-default", 400_000)
    # Track weitere 400k → kumuliert 800k → Warn (>500k), kein Hard.
    import logging as _logging
    limiter.track_token_usage("agent-default", 400_000)
