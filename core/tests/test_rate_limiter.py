"""
test_rate_limiter.py — Tests für RateLimiter (Agent-Call-Limiting + Token-Tracking)
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.rate_limiter import RateLimiter, RateLimitSettings


# ============================================================= Agent-Call-Limiting

def test_agent_call_erlaubt_bis_limit():
    rl = RateLimiter(settings=RateLimitSettings(agent_call_max=5, agent_call_window_s=60))
    for _ in range(5):
        rl.check_agent_call("agent-x")  # darf nicht werfen


def test_agent_call_blockiert_nach_limit():
    rl = RateLimiter(settings=RateLimitSettings(agent_call_max=3, agent_call_window_s=60))
    for _ in range(3):
        rl.check_agent_call("agent-y")
    with pytest.raises(RuntimeError, match="Call-Limit"):
        rl.check_agent_call("agent-y")


def test_agent_call_verschiedene_agents_unabhaengig():
    rl = RateLimiter(settings=RateLimitSettings(agent_call_max=2, agent_call_window_s=60))
    rl.check_agent_call("agent-a")
    rl.check_agent_call("agent-a")
    # agent-a ist jetzt voll, aber agent-b nicht
    with pytest.raises(RuntimeError):
        rl.check_agent_call("agent-a")
    rl.check_agent_call("agent-b")  # darf nicht werfen


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
