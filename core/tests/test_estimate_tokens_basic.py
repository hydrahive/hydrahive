"""Tests für estimate_tokens Edge-Cases (#387).

Leerer String → 0, einzelne Zeichen → mindestens 1 (per max(1,...)),
langer Text → proportionale Schätzung.
"""
import pytest
import importlib.util
from pathlib import Path


def _load_module():
    """Direktladen ohne __init__ (pydantic-frei)."""
    spec = importlib.util.spec_from_file_location(
        "token_estimation",
        Path(__file__).parent.parent / "src" / "hydrahive_core" / "token_estimation.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestEstimateTokensEdgeCases:
    """Edge-Case Tests für estimate_tokens."""

    def test_empty_string_returns_zero(self):
        """Leerer String gibt 0 zurück (kein min-1 Schutz)."""
        mod = _load_module()
        assert mod.estimate_tokens("") == 0

    def test_single_char_returns_at_least_one(self):
        """Ein einzelnes Zeichen gibt mindestens 1 Token zurück."""
        mod = _load_module()
        result = mod.estimate_tokens("x")
        assert result >= 1

    def test_short_strings_return_at_least_one(self):
        """Kurze Strings (1-3 Zeichen) geben mindestens 1 Token zurück."""
        mod = _load_module()
        for text in ("a", "ab", "abc"):
            result = mod.estimate_tokens(text)
            assert result >= 1, f"estimate_tokens({text!r}) sollte >= 1 sein, got {result}"

    def test_long_text_proportional(self):
        """Langer Text skaliert proportional (chars / 3.2)."""
        mod = _load_module()
        text = "a" * 320  # 320 chars → ~100 tokens
        result = mod.estimate_tokens(text)
        # Konservative Range: 80-120 tokens
        assert 80 <= result <= 120
