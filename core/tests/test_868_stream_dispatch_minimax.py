"""#868: stream_boss-Dispatch auf MiniMax-Anthropic-SDK-Pfad — Tests.

Prüft die reine Dispatch-Entscheidung (`_should_use_minimax_anthropic_stream`)
für alle Kombinationen aus Modell, Feature-Flag, Key-Verfügbarkeit und OAuth-
Präsenz. Ein Source-Invariant-Test sichert zusätzlich, dass `handle_message_
stream` den Helper tatsächlich aufruft und den neuen Pfad dispatched.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def _make_agent_cfg(model: str = "MiniMax-M2.7", api_key_env: str = "MINIMAX_API_KEY"):
    return SimpleNamespace(
        id="test-agent",
        llm=SimpleNamespace(
            model=model,
            fallback_models=[],
            temperature=0.7,
            max_tokens=4096,
            thinking_budget=0,
            api_key_env=api_key_env,
            ollama_base_url=None,
        ),
        agent_dir=None,
    )


# -------------------------------------------- pure predicate tests

def test_dispatch_routes_minimax_by_default(monkeypatch):
    """#870: Default (Flag ungesetzt) + MiniMax-Model + Key + kein OAuth
    → should_use=True."""
    from hydrahive_core.orchestrator_stream import _should_use_minimax_anthropic_stream

    monkeypatch.delenv("HYDRAHIVE_MINIMAX_ANTHROPIC_SDK", raising=False)
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-mm-test")

    cfg = _make_agent_cfg()
    use, key = _should_use_minimax_anthropic_stream("MiniMax-M2.7", cfg, oauth_token_present=False)
    assert use is True
    assert key == "sk-mm-test"


def test_dispatch_falls_through_on_explicit_opt_out(monkeypatch):
    """#870: Explizites Opt-Out mit HYDRAHIVE_MINIMAX_ANTHROPIC_SDK=0 →
    should_use=False. Nur fuer Rollback/Debugging."""
    from hydrahive_core.orchestrator_stream import _should_use_minimax_anthropic_stream

    monkeypatch.setenv("HYDRAHIVE_MINIMAX_ANTHROPIC_SDK", "0")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-mm-test")

    cfg = _make_agent_cfg()
    use, key = _should_use_minimax_anthropic_stream("MiniMax-M2.7", cfg, oauth_token_present=False)
    assert use is False
    assert key == ""


def test_dispatch_falls_through_when_no_key(monkeypatch):
    """Flag=1 aber kein MiniMax-Key → should_use=False."""
    from hydrahive_core.orchestrator_stream import _should_use_minimax_anthropic_stream

    monkeypatch.setenv("HYDRAHIVE_MINIMAX_ANTHROPIC_SDK", "1")
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)

    cfg = _make_agent_cfg(api_key_env="UNSET_VAR")
    use, key = _should_use_minimax_anthropic_stream("MiniMax-M2.7", cfg, oauth_token_present=False)
    assert use is False
    assert key == ""


def test_dispatch_falls_through_for_non_minimax_model(monkeypatch):
    """claude-*-Modell → should_use=False, auch wenn Flag=Default-ON."""
    from hydrahive_core.orchestrator_stream import _should_use_minimax_anthropic_stream

    monkeypatch.delenv("HYDRAHIVE_MINIMAX_ANTHROPIC_SDK", raising=False)

    cfg = _make_agent_cfg(model="claude-sonnet-4-6")
    use, key = _should_use_minimax_anthropic_stream("claude-sonnet-4-6", cfg, oauth_token_present=False)
    assert use is False
    assert key == ""


def test_dispatch_yields_to_oauth_when_token_present(monkeypatch):
    """Wenn OAuth-Token aktiv ist, hat der OAuth-Pfad Vorrang — MiniMax-Pfad
    wird nicht genommen (auch nicht wenn Modell+Flag+Key stimmen)."""
    from hydrahive_core.orchestrator_stream import _should_use_minimax_anthropic_stream

    monkeypatch.delenv("HYDRAHIVE_MINIMAX_ANTHROPIC_SDK", raising=False)
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-mm-test")

    cfg = _make_agent_cfg()
    use, key = _should_use_minimax_anthropic_stream("MiniMax-M2.7", cfg, oauth_token_present=True)
    assert use is False


# -------------------------------------------- source-invariant

def test_handle_message_stream_dispatches_minimax_anthropic_sdk():
    """handle_message_stream ruft _should_use_minimax_anthropic_stream auf UND
    hat eine _stream_minimax_anthropic-Branch. Sichert gegen versehentliches
    Entfernen des Dispatch-Switches."""
    import inspect
    from hydrahive_core import orchestrator_stream

    src = inspect.getsource(orchestrator_stream.handle_message_stream)
    assert "_should_use_minimax_anthropic_stream" in src, (
        "handle_message_stream nutzt den Dispatch-Helper nicht — "
        "#868-Switch ist weg"
    )
    assert "_stream_minimax_anthropic" in src, (
        "handle_message_stream dispatched nicht auf _stream_minimax_anthropic"
    )


def test_handle_message_stream_preserves_fallback_paths():
    """Nach #868 müssen _stream_anthropic_oauth und _stream_litellm weiter
    erreichbar sein — Regressions-Schutz."""
    import inspect
    from hydrahive_core import orchestrator_stream

    src = inspect.getsource(orchestrator_stream.handle_message_stream)
    assert "_stream_anthropic_oauth" in src
    assert "_stream_litellm" in src
    assert "_stream_codex" in src
