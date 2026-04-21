"""
test_llm_provider_check_trace.py — Diagnostischer Trace im Hint.

Ohne Core-Log-Zugriff (Kunden-Deploy) muss der Hint selbst klar sagen
was geprüft wurde und warum kein Match fiel.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _clean_env(monkeypatch):
    for k in ("NVIDIA_API_KEY", "MINIMAX_API_KEY", "OPENAI_API_KEY",
              "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def test_hint_includes_trace_for_nvidia(monkeypatch):
    from hydrahive_core.orchestrator_llm import check_llm_provider_available

    _clean_env(monkeypatch)
    with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value={"providers": {}}), \
         patch("hydrahive_core.orchestrator_llm._load_claude_oauth_token", return_value=None), \
         patch("hydrahive_core.orchestrator_llm._load_openai_codex_token", return_value=None):
        result = check_llm_provider_available(["nvidia/llama-3.3-nemotron-super-49b-v1.5"])

    assert result is not None, "Ohne Key muss Hint kommen"
    assert "Geprüft beim Aufruf" in result
    assert "nvidia/llama-3.3-nemotron-super-49b-v1.5" in result
    assert "[nvidia]" in result
    assert "providers.nvidia.api_key" in result
    # Die vollständige Provider-Liste muss weiterhin drin sein
    assert "NVIDIA NIM" in result


def test_hint_trace_for_unknown_model(monkeypatch):
    from hydrahive_core.orchestrator_llm import check_llm_provider_available

    _clean_env(monkeypatch)
    with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value={"providers": {}}), \
         patch("hydrahive_core.orchestrator_llm._load_claude_oauth_token", return_value=None), \
         patch("hydrahive_core.orchestrator_llm._load_openai_codex_token", return_value=None):
        # "vendor/typo-model" hat "/" aber passt in keine bekannte Kategorie
        # (kein claude-prefix, kein openai-, nicht in NVIDIA_MODELS, kein
        # minimax-prefix, kein ollama-prefix). Sollte weder ollama sein,
        # weil "/" drin ist. Fällt in "unknown".
        result = check_llm_provider_available(["xai/grok-beta"])

    assert result is not None
    # xai/grok-beta ist weder claude/openai/minimax/nvidia noch
    # ollama (weil "/" drin) — landet in [unknown]
    assert "xai/grok-beta" in result


def test_hint_trace_for_multiple_models(monkeypatch):
    from hydrahive_core.orchestrator_llm import check_llm_provider_available

    _clean_env(monkeypatch)
    with patch("hydrahive_core.orchestrator_llm._load_llm_config", return_value={"providers": {}}), \
         patch("hydrahive_core.orchestrator_llm._load_claude_oauth_token", return_value=None), \
         patch("hydrahive_core.orchestrator_llm._load_openai_codex_token", return_value=None):
        result = check_llm_provider_available([
            "nvidia/llama-3.3-nemotron-super-49b-v1.5",
            "claude-sonnet-4-6",
            "gpt-4o",
        ])
    assert result is not None
    # Alle drei Modelle sollten im Trace auftauchen
    for token in ("[nvidia]", "[claude]", "[openai]"):
        assert token in result, f"{token} fehlt in Trace"


def test_no_trace_when_provider_ok(monkeypatch):
    """Wenn ein Provider verfügbar ist → None → kein Hint → kein Trace."""
    from hydrahive_core.orchestrator_llm import check_llm_provider_available
    _clean_env(monkeypatch)
    with patch("hydrahive_core.orchestrator_llm._load_llm_config",
               return_value={"providers": {"nvidia": {"api_key": "nvapi-foo"}}}), \
         patch("hydrahive_core.orchestrator_llm._load_claude_oauth_token", return_value=None), \
         patch("hydrahive_core.orchestrator_llm._load_openai_codex_token", return_value=None):
        result = check_llm_provider_available(["nvidia/llama-3.3-nemotron-super-49b-v1.5"])
    assert result is None
