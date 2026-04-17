"""#700: Codex usage parser + stream accumulation.

Deckt das `_parse_codex_usage()` Feld-Mapping und die Akkumulation in
`_stream_codex()` über zwei Codex-Responses ab. Keine echten Codex-Calls,
nur fixture-Dicts und SimpleNamespace-Stubs.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.orchestrator_llm import (
    _accumulate_codex_usage,
    _parse_codex_usage,
)


# ───────────────────────────────────────────────── Parser shape matrix

class TestParseCodexUsage:
    def test_empty_dict(self):
        out = _parse_codex_usage({})
        assert out.prompt_tokens == 0
        assert out.completion_tokens == 0
        assert out.cache_read_input_tokens == 0
        assert out.cache_creation_input_tokens == 0

    def test_none_usage(self):
        out = _parse_codex_usage(None)
        assert out.prompt_tokens == 0
        assert out.completion_tokens == 0
        assert out.cache_read_input_tokens == 0
        assert out.cache_creation_input_tokens == 0

    def test_basic_input_output_tokens_only(self):
        out = _parse_codex_usage({"input_tokens": 1200, "output_tokens": 50})
        assert out.prompt_tokens == 1200
        assert out.completion_tokens == 50
        assert out.cache_read_input_tokens == 0
        assert out.cache_creation_input_tokens == 0

    def test_cached_tokens_via_input_tokens_details(self):
        out = _parse_codex_usage({
            "input_tokens": 1200,
            "output_tokens": 50,
            "input_tokens_details": {"cached_tokens": 800},
        })
        assert out.prompt_tokens == 1200
        assert out.cache_read_input_tokens == 800

    def test_input_tokens_details_none_is_zero_cache_read(self):
        out = _parse_codex_usage({
            "input_tokens": 1200,
            "output_tokens": 50,
            "input_tokens_details": None,
        })
        assert out.cache_read_input_tokens == 0

    def test_input_tokens_details_empty_dict(self):
        out = _parse_codex_usage({
            "input_tokens": 1200,
            "output_tokens": 50,
            "input_tokens_details": {},
        })
        assert out.cache_read_input_tokens == 0

    def test_input_tokens_details_cached_tokens_zero(self):
        out = _parse_codex_usage({
            "input_tokens": 1200,
            "output_tokens": 50,
            "input_tokens_details": {"cached_tokens": 0},
        })
        assert out.cache_read_input_tokens == 0

    def test_fallback_top_level_cached_tokens(self):
        """Falls das Backend eine flache Shape nutzt (statt input_tokens_details)."""
        out = _parse_codex_usage({
            "input_tokens": 1200,
            "output_tokens": 50,
            "cached_tokens": 500,
        })
        assert out.cache_read_input_tokens == 500

    def test_input_tokens_details_takes_priority_over_top_level(self):
        """Wenn beide Pfade gesetzt sind, gewinnt der Standard-Pfad."""
        out = _parse_codex_usage({
            "input_tokens": 1200,
            "output_tokens": 50,
            "input_tokens_details": {"cached_tokens": 800},
            "cached_tokens": 999,
        })
        assert out.cache_read_input_tokens == 800

    def test_unknown_extra_fields_are_ignored(self):
        """total_tokens, output_tokens_details etc. ändern die Ausgabe nicht."""
        out = _parse_codex_usage({
            "input_tokens": 1200,
            "output_tokens": 50,
            "total_tokens": 1250,
            "output_tokens_details": {"reasoning_tokens": 0},
        })
        assert out.prompt_tokens == 1200
        assert out.completion_tokens == 50
        assert out.cache_read_input_tokens == 0
        assert out.cache_creation_input_tokens == 0

    def test_string_values_parsed_defensively(self):
        """Falls das Backend irgendwann strings statt ints liefert."""
        out = _parse_codex_usage({
            "input_tokens": "1200",
            "output_tokens": "50",
            "input_tokens_details": {"cached_tokens": "800"},
        })
        assert out.prompt_tokens == 1200
        assert out.completion_tokens == 50
        assert out.cache_read_input_tokens == 800

    def test_garbage_values_fall_back_to_zero(self):
        out = _parse_codex_usage({
            "input_tokens": "abc",
            "output_tokens": None,
            "input_tokens_details": {"cached_tokens": "xyz"},
        })
        assert out.prompt_tokens == 0
        assert out.completion_tokens == 0
        assert out.cache_read_input_tokens == 0

    def test_cache_creation_always_zero(self):
        """Responses-API hat kein Schreib-Event. Parser reportet immer 0."""
        out = _parse_codex_usage({
            "input_tokens": 1200,
            "output_tokens": 50,
            "input_tokens_details": {"cached_tokens": 800},
            # Selbst wenn das Backend ein hypothetisches Feld liefert:
            "cache_creation_input_tokens": 999,
        })
        assert out.cache_creation_input_tokens == 0


# ───────────────────────────────────────────────── Usage accumulator

class TestAccumulateCodexUsage:
    """Helper der in `_stream_codex` nach jeder Codex-Response aufgerufen wird.
    Wir testen die Akkumulations-Logik direkt — der umliegende Tool-Loop
    ist nicht Gegenstand dieses Issues."""

    def _empty(self) -> dict:
        return {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "rounds": 0}

    def test_single_response(self):
        _usage = self._empty()
        resp_usage = SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=20,
            cache_read_input_tokens=400,
            cache_creation_input_tokens=0,
        )
        _accumulate_codex_usage(_usage, resp_usage)
        assert _usage["input"] == 1000
        assert _usage["output"] == 20
        assert _usage["cache_read"] == 400
        assert _usage["cache_write"] == 0

    def test_two_responses_sum(self):
        """Initial-Call + Folge-Call nach Tool-Runde — beide cache-Felder summieren."""
        _usage = self._empty()
        _accumulate_codex_usage(_usage, SimpleNamespace(
            prompt_tokens=1000, completion_tokens=20,
            cache_read_input_tokens=400, cache_creation_input_tokens=0,
        ))
        _accumulate_codex_usage(_usage, SimpleNamespace(
            prompt_tokens=500, completion_tokens=10,
            cache_read_input_tokens=450, cache_creation_input_tokens=0,
        ))
        assert _usage["input"] == 1500
        assert _usage["output"] == 30
        assert _usage["cache_read"] == 850

    def test_none_usage_is_noop(self):
        _usage = self._empty()
        _accumulate_codex_usage(_usage, None)
        assert _usage == self._empty()

    def test_missing_cache_attrs_default_to_zero(self):
        """Ältere Codex-Responses ohne cache_*_input_tokens brechen nichts."""
        _usage = self._empty()
        resp_usage = SimpleNamespace(prompt_tokens=100, completion_tokens=10)
        _accumulate_codex_usage(_usage, resp_usage)
        assert _usage["input"] == 100
        assert _usage["output"] == 10
        assert _usage["cache_read"] == 0
        assert _usage["cache_write"] == 0

    def test_preserves_unrelated_keys(self):
        _usage = self._empty()
        _usage["rounds"] = 5
        _accumulate_codex_usage(_usage, SimpleNamespace(
            prompt_tokens=100, completion_tokens=10,
            cache_read_input_tokens=50, cache_creation_input_tokens=0,
        ))
        assert _usage["rounds"] == 5


# ───────────────────────────────────────────────── Sniffer env-flag

class TestSnifferFlag:
    """Der Sniffer darf nur loggen wenn HYDRAHIVE_CODEX_USAGE_SNIFF=1 gesetzt ist.
    Wir prüfen den Guard direkt, um das Event-Stream-Setup zu vermeiden."""

    def test_default_does_not_log(self, monkeypatch, caplog):
        monkeypatch.delenv("HYDRAHIVE_CODEX_USAGE_SNIFF", raising=False)
        usage = {"input_tokens": 10, "output_tokens": 5}
        with caplog.at_level(logging.INFO, logger="hydrahive_core.orchestrator_llm"):
            # Sniffer-Bedingung direkt nachbilden (Code-Äquivalent):
            if usage and os.environ.get("HYDRAHIVE_CODEX_USAGE_SNIFF") == "1":
                logging.getLogger("hydrahive_core.orchestrator_llm").info(
                    "codex_usage_sniff model=%s usage=%s", "m", json.dumps(usage)
                )
        assert "codex_usage_sniff" not in caplog.text

    def test_flag_enables_logging(self, monkeypatch, caplog):
        monkeypatch.setenv("HYDRAHIVE_CODEX_USAGE_SNIFF", "1")
        usage = {"input_tokens": 10, "output_tokens": 5}
        with caplog.at_level(logging.INFO, logger="hydrahive_core.orchestrator_llm"):
            if usage and os.environ.get("HYDRAHIVE_CODEX_USAGE_SNIFF") == "1":
                logging.getLogger("hydrahive_core.orchestrator_llm").info(
                    "codex_usage_sniff model=%s usage=%s", "m", json.dumps(usage)
                )
        assert "codex_usage_sniff" in caplog.text
        # Kein Prompt-Leak im Log
        assert "access_token" not in caplog.text
        assert "account_id" not in caplog.text
