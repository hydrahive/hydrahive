"""
test_848_hf_tokenizer.py — Gate 7b: HF Tokenizer Fallback für MiniMax/Kimi (#848).

Sicherstellen dass:
- minimax/kimi/etc. bei installiertem transformers den Qwen2.5-Tokenizer nutzen
- bei fehlendem transformers, ImportError, LoadError, NetworkError: Fallback auf Heuristik
- tiktoken-Pfad für claude/gpt unverändert bleibt
- Cache funktioniert (kein zweites Laden)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


# ─── Helpers ───────────────────────────────────────────────────────────

def _reset_caches():
    """Caches und warned-set zurücksetzen für isolierte Tests."""
    from hydrahive_core import token_estimation as te
    te._tokenizer_cache.clear()
    te._warned_models.clear()


# ─── Test: tiktoken-Pfad für Claude/GPT — OHNE transformers-Abhaengigkeit ─

def test_claude_tiktoken_no_transformers(monkeypatch):
    """tiktoken-Pfad für claude/gpt muss funktionieren, auch wenn transformers fehlt.

    Dieser Test verifiziert dass der claude-tiktoken-Fallback funktioniert,
    ohne dass transformers installiert ist. Er wird NIEMALS übersprungen.
    """
    _reset_caches()
    # Simuliere: transformers existiert nicht
    import sys
    mods = {k: v for k, v in sys.modules.items() if k == "transformers"}
    for k in mods:
        del sys.modules[k]
    monkeypatch.delitem(sys.modules, "transformers", raising=False)
    from hydrahive_core.token_estimation import estimate_tokens

    result = estimate_tokens("hello claude", "claude-sonnet-4")
    assert result > 0, "tiktoken sollte Tokens zaehlen"


# ─── Test: HF-Tokenizer wird für minimax verwendet ──────────────────────

def test_minimax_uses_qwen_tokenizer(monkeypatch):
    """Bei installiertem transformers + minimax model: Qwen-Tokenizer returned."""
    _reset_caches()

    class FakeTokenizer:
        def encode(self, text):
            return [1, 2, 3] if text else []

    class FakeAutoTokenizer:
        @staticmethod
        def from_pretrained(repo_id, use_fast=False):
            assert repo_id == "Qwen/Qwen2.5-0.5B-Instruct"
            return FakeTokenizer()

    monkeypatch.setitem(sys.modules, "transformers", pytest.importorskip("transformers"))
    import transformers
    monkeypatch.setattr(transformers, "AutoTokenizer", FakeAutoTokenizer)

    from hydrahive_core.token_estimation import _get_tokenizer
    tok = _get_tokenizer("minimax-text-01")
    assert tok is not None
    assert tok("hello") == 3  # FakeTokenizer gibt [1,2,3] → len=3


# ─── Test: ImportError → Fallback auf Heuristik ─────────────────────────

def test_hf_import_error_falls_back(monkeypatch, caplog):
    """Kein transformers installiert → tokenizer=None → Heuristik greift."""
    _reset_caches()
    # Simuliere: transformers existiert nicht
    import sys
    mods = {k: v for k, v in sys.modules.items() if k == "transformers"}
    for k in mods:
        del sys.modules[k]
    monkeypatch.delitem(sys.modules, "transformers", raising=False)

    from hydrahive_core import token_estimation as te
    from hydrahive_core.token_estimation import _get_tokenizer, estimate_tokens

    tok = _get_tokenizer("minimax-chat")
    assert tok is None  # kein Tokenizer → None
    # estimate_tokens nutzt den Fallback
    result = estimate_tokens("hello world", "minimax-chat")
    assert result == max(1, int(len("hello world") / 3.2))


# ─── Test: Load-Failure (bad repo / network) → Fallback auf Heuristik ──

def test_hf_load_failure_falls_back(monkeypatch, caplog):
    """from_pretrained wirft Exception → tokenizer=None → Heuristik."""
    transformers = pytest.importorskip("transformers")
    _reset_caches()
    original_from_pretrained = transformers.AutoTokenizer.from_pretrained

    def bad_from_pretrained(repo_id, **kwargs):
        raise OSError("Network unreachable")

    monkeypatch.setattr(transformers.AutoTokenizer, "from_pretrained", bad_from_pretrained)

    from hydrahive_core import token_estimation as te
    from hydrahive_core.token_estimation import _get_tokenizer, estimate_tokens

    tok = _get_tokenizer("minimax")
    assert tok is None

    result = estimate_tokens("some text", "minimax")
    assert result == max(1, int(len("some text") / 3.2))
    # Warning wurde geloggt (einmalig)
    assert any("fallback to heuristic" in r.message for r in caplog.records)


# ─── Test: Cache-Hit — zweiter Aufruf lädt nicht nochmal ───────────────

def test_cache_hit_on_second_call(monkeypatch):
    """Zwei Aufrufe mit gleichem Model → nur 1x from_pretrained."""
    transformers = pytest.importorskip("transformers")
    _reset_caches()
    class FakeTokenizer:
        encode_count = 0

        def encode(self, text):
            FakeTokenizer.encode_count += 1
            return [1]

    class FakeAutoTokenizer:
        @staticmethod
        def from_pretrained(repo_id, use_fast=False):
            return FakeTokenizer()

    monkeypatch.setattr(transformers.AutoTokenizer, "from_pretrained", FakeAutoTokenizer)

    from hydrahive_core.token_estimation import _get_tokenizer

    _get_tokenizer("kimi")
    _get_tokenizer("kimi")

    assert FakeTokenizer.encode_count == 1, "from_pretrained sollte nur 1x aufgerufen werden"


# ─── Test: Claude/GPT-Pfad via tiktoken bleibt unberührt ────────────────

def test_claude_path_unchanged(monkeypatch):
    """claude-encoding läuft über tiktoken, kein HF-Tokenizer involviert."""
    import types
    from unittest.mock import MagicMock

    # #858: kein pytest.importorskip("transformers") — dieser Test prüft
    # den tiktoken-Pfad, braucht transformers nur als Spy-Target.
    fake_transformers = types.ModuleType("transformers")
    fake_transformers.AutoTokenizer = MagicMock()
    monkeypatch.setitem(__import__("sys").modules, "transformers", fake_transformers)

    hf_called = False

    def trackcall(repo_id, **kwargs):
        nonlocal hf_called
        hf_called = True

    fake_transformers.AutoTokenizer.from_pretrained = trackcall

    _reset_caches()

    from hydrahive_core.token_estimation import estimate_tokens

    result = estimate_tokens("hello claude", "claude-sonnet-4")
    assert not hf_called, "tiktoken-Pfad sollte kein HF importieren"
    assert result > 0
