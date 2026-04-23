"""
test_rate_limiter.py — Tests für RateLimiter (Agent-Call-Limiting + Token-Tracking)
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.rate_limiter import (
    RateLimiter,
    RateLimitSettings,
    TokenBudgetExceeded,
)


# ============================================================= Agent-Call-Limiting

@pytest.mark.skip(reason="#779: check_agent_call braucht Redis, kein lokaler Fallback mehr — fakeredis nicht im test env")
def test_agent_call_erlaubt_bis_limit():
    rl = RateLimiter(settings=RateLimitSettings(agent_call_max=5, agent_call_window_s=60))
    for _ in range(5):
        rl.check_agent_call("agent-x")  # darf nicht werfen


@pytest.mark.skip(reason="#779: check_agent_call braucht Redis, kein lokaler Fallback mehr — fakeredis nicht im test env")
def test_agent_call_blockiert_nach_limit():
    rl = RateLimiter(settings=RateLimitSettings(agent_call_max=3, agent_call_window_s=60))
    for _ in range(3):
        rl.check_agent_call("agent-y")
    with pytest.raises(RuntimeError, match="Call-Limit"):
        rl.check_agent_call("agent-y")


@pytest.mark.skip(reason="#779: check_agent_call braucht Redis, kein lokaler Fallback mehr — fakeredis nicht im test env")
def test_agent_call_verschiedene_agents_unabhaengig():
    rl = RateLimiter(settings=RateLimitSettings(agent_call_max=2, agent_call_window_s=60))
    rl.check_agent_call("agent-a")
    rl.check_agent_call("agent-a")
    # agent-a ist jetzt voll, aber agent-b nicht
    with pytest.raises(RuntimeError):
        rl.check_agent_call("agent-a")
    rl.check_agent_call("agent-b")  # darf nicht werfen


@pytest.mark.skip(reason="#779: check_agent_call braucht Redis, kein lokaler Fallback mehr — fakeredis nicht im test env")
def test_agent_call_window_reset():
    """Nach Ablauf des Fensters darf der Agent wieder callen."""
    rl = RateLimiter(settings=RateLimitSettings(agent_call_max=2, agent_call_window_s=1))
    rl.check_agent_call("agent-c")
    rl.check_agent_call("agent-c")
    with pytest.raises(RuntimeError):
        rl.check_agent_call("agent-c")
    # Fenster ablaufen lassen
    time.sleep(1.05)
    rl.check_agent_call("agent-c")  # darf wieder


# ============================================================= Token-Tracking

def test_token_tracking_kein_warning_unter_limit(caplog):
    rl = RateLimiter(settings=RateLimitSettings(agent_token_warn_per_hour=10_000))
    import logging
    with caplog.at_level(logging.WARNING):
        rl.track_token_usage("agent-tok", 5_000)
    assert "Token-Budget" not in caplog.text


def test_token_tracking_warning_ueber_limit(caplog):
    rl = RateLimiter(settings=RateLimitSettings(agent_token_warn_per_hour=1_000))
    import logging
    with caplog.at_level(logging.WARNING):
        rl.track_token_usage("agent-tok2", 1_500)
    assert "Token-Budget" in caplog.text


def test_get_token_usage_hour():
    rl = RateLimiter(settings=RateLimitSettings(agent_token_warn_per_hour=100_000))
    rl.track_token_usage("agent-z", 3_000)
    rl.track_token_usage("agent-z", 2_000)
    assert rl.get_token_usage_hour("agent-z") == 5_000


def test_get_token_usage_hour_unbekannter_agent():
    rl = RateLimiter()
    assert rl.get_token_usage_hour("unbekannt") == 0


# ============================================================= #750 Hard-Stop

def test_token_hard_stop_raises_on_overflow():
    """Einzelner Track-Call der das Hard-Limit überschreitet raist."""
    rl = RateLimiter(settings=RateLimitSettings(
        agent_token_warn_per_hour=100_000,
        agent_token_hard_per_hour=200_000,
    ))
    with pytest.raises(TokenBudgetExceeded) as exc:
        rl.track_token_usage("agent-hard", 250_000)
    assert exc.value.agent_id == "agent-hard"
    assert exc.value.tokens_used == 250_000
    assert exc.value.limit == 200_000


def test_token_hard_stop_accumulates_over_multiple_calls():
    """Mehrere Track-Calls akkumulieren bis Hard-Limit erreicht ist."""
    rl = RateLimiter(settings=RateLimitSettings(
        agent_token_warn_per_hour=100_000,
        agent_token_hard_per_hour=100_000,
    ))
    rl.track_token_usage("agent-acc", 40_000)  # 40k, ok
    rl.track_token_usage("agent-acc", 40_000)  # 80k, ok
    with pytest.raises(TokenBudgetExceeded):
        rl.track_token_usage("agent-acc", 30_000)  # 110k, über hard


def test_token_hard_stop_disabled_wenn_zero():
    """agent_token_hard_per_hour=0 → Hard-Stop disabled."""
    rl = RateLimiter(settings=RateLimitSettings(
        agent_token_warn_per_hour=1_000,
        agent_token_hard_per_hour=0,
    ))
    # Millionen Tokens, kein Raise
    rl.track_token_usage("agent-disabled", 5_000_000)
    rl.track_token_usage("agent-disabled", 5_000_000)
    assert rl.get_token_usage_hour("agent-disabled") == 10_000_000


def test_token_hard_stop_audit_log(caplog):
    """Audit-Log wird bei Hard-Stop geschrieben."""
    import logging
    rl = RateLimiter(settings=RateLimitSettings(
        agent_token_warn_per_hour=500,
        agent_token_hard_per_hour=1_000,
    ))
    with caplog.at_level(logging.ERROR):
        with pytest.raises(TokenBudgetExceeded):
            rl.track_token_usage("agent-audit", 2_000)
    assert "AUDIT[token_budget_hard]" in caplog.text
    assert "agent-audit" in caplog.text


def test_token_warn_alone_does_not_raise():
    """Warn-Threshold überschreiten raist nicht wenn Hard noch nicht erreicht."""
    rl = RateLimiter(settings=RateLimitSettings(
        agent_token_warn_per_hour=1_000,
        agent_token_hard_per_hour=10_000,
    ))
    # Warn greift, Hard nicht → kein Raise
    rl.track_token_usage("agent-warn", 1_500)
    assert rl.get_token_usage_hour("agent-warn") == 1_500


def test_check_token_budget_ok_unter_limit():
    """check_token_budget raist nicht wenn unter Hard-Limit."""
    rl = RateLimiter(settings=RateLimitSettings(
        agent_token_hard_per_hour=100_000,
    ))
    rl.track_token_usage("agent-chk", 50_000)
    rl.check_token_budget("agent-chk")  # darf nicht werfen


def test_check_token_budget_raises_ueber_limit():
    """check_token_budget raist wenn Hard-Limit bereits erreicht."""
    rl = RateLimiter(settings=RateLimitSettings(
        agent_token_warn_per_hour=100_000,
        agent_token_hard_per_hour=100_000,
    ))
    # Akkumuliere knapp unter Hard (ohne Raise)
    rl.track_token_usage("agent-chk2", 90_000)
    # Dann einen Call direkt über Hard — track raist hier schon
    with pytest.raises(TokenBudgetExceeded):
        rl.track_token_usage("agent-chk2", 20_000)
    # check_token_budget sieht den akkumulierten Stand UND raist weiter
    with pytest.raises(TokenBudgetExceeded):
        rl.check_token_budget("agent-chk2")


def test_check_token_budget_disabled_wenn_zero():
    """check_token_budget mit hard=0 ist no-op."""
    rl = RateLimiter(settings=RateLimitSettings(
        agent_token_hard_per_hour=0,
    ))
    rl.track_token_usage("agent-free", 10_000_000)
    rl.check_token_budget("agent-free")  # darf nicht werfen


# ============================================================= Tool-Registry-Wiring

def test_ask_agent_tool_check_agent_call():
    """AskAgentTool ruft check_agent_call auf wenn _rate_limiter gesetzt ist."""
    from unittest.mock import MagicMock, patch
    from hydrahive_core import tool_registry as tr

    mock_rl = MagicMock()
    mock_rl.check_agent_call.side_effect = RuntimeError("blockiert")

    with patch.object(tr, "_rate_limiter", mock_rl):
        tool = tr.AskAgentTool()
        import asyncio
        with pytest.raises(RuntimeError, match="blockiert"):
            asyncio.run(tool.execute("agent-src", "proj-1", target="agent-dst", question="hallo"))
        mock_rl.check_agent_call.assert_called_once_with("agent-src")


# v2 (#588): DelegateAgentTool wurde entfernt (war v1 Worker-Dispatch).
# Ersatz: AskAgentTool (ask_agent) — testen wir in #590 mit v2-Fixtures.
