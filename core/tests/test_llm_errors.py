"""
test_llm_errors.py — format_llm_error klassifiziert + scrubbt Secrets.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class FakeAuthError(Exception):
    """Simuliert anthropic.AuthenticationError / openai.AuthenticationError."""


class FakeBadRequestError(Exception):
    pass


class FakeRateLimitError(Exception):
    pass


class FakeTimeoutError(Exception):
    pass


def test_format_auth_error_gives_hint():
    from hydrahive_core.llm_errors import format_llm_error
    e = FakeAuthError("401 Unauthorized: invalid api key")
    out = format_llm_error(e)
    assert "Authent" in out or "401" in out
    assert "API-Key" in out
    assert out.startswith("[Fehler]")


def test_format_ratelimit():
    from hydrahive_core.llm_errors import format_llm_error
    e = FakeRateLimitError("429 Too Many Requests — quota exceeded")
    out = format_llm_error(e)
    assert "Rate-Limit" in out
    assert "429" in out


def test_format_badrequest_model_not_found():
    from hydrahive_core.llm_errors import format_llm_error
    e = FakeBadRequestError("400 The model 'nvidia/xyz' does not exist")
    out = format_llm_error(e)
    assert "ungültig" in out or "Modell" in out


def test_format_timeout():
    from hydrahive_core.llm_errors import format_llm_error
    e = FakeTimeoutError("Connection timeout after 60s")
    out = format_llm_error(e)
    assert "Timeout" in out or "nicht erreichbar" in out


def test_secrets_are_scrubbed():
    """Bearer-Tokens, sk-*, nvapi-* in Exception-Messages dürfen NICHT im
    user-facing String erscheinen."""
    from hydrahive_core.llm_errors import format_llm_error

    cases = [
        ("401 with header Authorization: Bearer sk-abc123def456ghi789jkl",
         ["sk-abc123def456", "Bearer sk-"]),
        ("NVIDIA call failed, key=nvapi-1234567890ABCDefgh",
         ["nvapi-1234567890"]),
        ("cookie: session=xyz789abc123", ["xyz789abc123"]),
        ("github call: ghp_AAAAAAAAAAAAAAAAAAAA failed",
         ["ghp_AAAAAAAAAAAAAAAAAAAA"]),
    ]
    for raw, forbidden in cases:
        out = format_llm_error(FakeAuthError(raw))
        for secret in forbidden:
            assert secret not in out, \
                f"Secret '{secret}' leckt in user-facing error: {out!r}"


def test_unknown_exception_gives_type_and_msg():
    """Auch völlig unbekannte Exceptions dürfen nicht still zu generisch
    mutieren — Typ + Message müssen durch."""
    from hydrahive_core.llm_errors import format_llm_error

    class WeirdProviderError(Exception):
        pass

    out = format_llm_error(WeirdProviderError("model 'foo' requires premium plan"))
    assert "WeirdProviderError" in out
    assert "foo" in out


def test_empty_message_does_not_crash():
    from hydrahive_core.llm_errors import format_llm_error
    out = format_llm_error(Exception(""))
    assert "[Fehler]" in out


def test_long_messages_are_truncated():
    from hydrahive_core.llm_errors import format_llm_error
    long = "X" * 2000
    out = format_llm_error(Exception(long))
    # Hart capped bei 240 + etwas Prefix-Text — unter 500 Zeichen total
    assert len(out) < 500
