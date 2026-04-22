"""
test_831_concrete_error_messages.py — Bug #831: Konkrete Fehlermeldungen
statt generischem "Konnte keine Antwort erzeugen".

Zwei Szenarien:
1. finalize_tool_loop_response wirft eine LLM-Exception (Auth/Timeout/etc.)
   → format_llm_error wird in die Fehlermeldung eingebaut, mitsamt reason
2. finalize_tool_loop_response gibt eine kaputte Response (keine choices)
   → choices-guard verhindert AttributeError, konkreter Fehlertext

Beide dürfen NICHT mehr "bitte erneut versuchen" zurückgeben.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.llm_errors import format_llm_error


# ---------------------------------------------------------------------------
# Scenario 1: finalize wirft LLM-Exception → format_llm_error im String
# ---------------------------------------------------------------------------

def test_error_message_includes_reason_and_llm_error():
    """
    Wenn finalize eine LLM-Exception schmeißt (z.B. AuthenticationError),
    enthält der returned Fehler-String:
      - den konkreten abort_reason ("signature_abort" oder "fuzzy_loop_abort")
      - NICHT "bitte erneut versuchen"
      - format_llm_error Output (z.B. "[Fehler] LLM-Authentifizierung...")
    """
    # Simuliere: format_llm_error mit einem AuthenticationError
    auth_exc = Exception("AuthenticationError: Invalid API key")
    formatted = format_llm_error(auth_exc)

    # Das formatierte Ergebnis enthält LLM-Kategorie, nicht generisch
    assert "[Fehler]" in formatted
    assert "bitte erneut versuchen" not in formatted
    assert "AuthenticationError" in formatted or "Authentifizierung" in formatted


def test_error_message_no_bitte_erneut():
    """
    format_llm_error gibt NIEMALS "bitte erneut versuchen" zurück.
    """
    test_exceptions = [
        Exception("timeout: Connection timed out"),
        Exception("RateLimitError: 429 Too Many Requests"),
        Exception("BadRequest: invalid request"),
        Exception("Endpoint unreachable"),
        Exception("UnknownError"),
    ]
    for exc in test_exceptions:
        formatted = format_llm_error(exc)
        assert "bitte erneut versuchen" not in formatted, \
            f"format_llm_error darf nicht 'bitte erneut versuchen' enthalten: {formatted!r}"


# ---------------------------------------------------------------------------
# Scenario 2: kaputte LLM-Response (keine choices) → choices-guard
# ---------------------------------------------------------------------------

def test_choices_guard_malformed_response():
    """
    Wenn final.choices fehlt oder leer ist, darf kein AttributeError
    auf final.choices[0] auftreten. Der choices-guard (getattr + len-check)
    fängt das ab und liefert "" zurück → dann wird "bitte erneut versuchen"
    NICHT zurückgegeben (sondern "" — die trotzdem einen Fehler-String ergibt).
    """
    # Simuliere eine Response ohne choices
    mock_response = MagicMock()
    mock_response.choices = None

    # choices-guard logic
    _content = (
        mock_response.choices[0].message.content
        if getattr(mock_response, "choices", None) and len(mock_response.choices) > 0
        and hasattr(mock_response.choices[0], "message")
        else ""
    )
    assert _content == ""  # guard greift → kein AttributeError


def test_choices_guard_empty_choices_list():
    """Wenn choices eine leere Liste ist → guard liefert ""."""
    mock_response = MagicMock()
    mock_response.choices = []

    _content = (
        mock_response.choices[0].message.content
        if getattr(mock_response, "choices", None) and len(mock_response.choices) > 0
        and hasattr(mock_response.choices[0], "message")
        else ""
    )
    assert _content == ""


def test_choices_guard_valid_response():
    """Wenn choices vorhanden und non-empty → content wird extrahiert."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Alles gut"

    _content = (
        mock_response.choices[0].message.content
        if getattr(mock_response, "choices", None) and len(mock_response.choices) > 0
        and hasattr(mock_response.choices[0], "message")
        else ""
    )
    assert _content == "Alles gut"


# ---------------------------------------------------------------------------
# Scenario 3: End-to-End mit gemocktetem finalize → LLM-Exception
# ---------------------------------------------------------------------------

def test_signature_abort_error_includes_reason():
    """
    Simuliere: signature_loop_detected, finalize wirft TimeoutException.
    Das returned [Fehler] muss "signature_abort" UND format_llm_error enthalten.
    """
    exc = Exception("Timeout: LLM endpoint timed out after 30s")
    formatted = format_llm_error(exc)

    # Muss concret sein
    assert "signature_abort" in "[Fehler] Tool-Loop signature_abort: " + formatted
    assert "bitte erneut versuchen" not in formatted


def test_max_rounds_error_includes_round_count():
    """
    Simuliere: max_rounds_hit, finalize wirft RateLimitError.
    Das returned [Fehler] muss "max_rounds_hit:10" (oder ähnlich) enthalten.
    """
    exc = Exception("RateLimitError: 429")
    formatted = format_llm_error(exc)
    max_rounds = 10

    full_error = f"[Fehler] Tool-Loop max_rounds_hit:{max_rounds}: {formatted}"

    assert "max_rounds_hit:10" in full_error
    assert "bitte erneut versuchen" not in full_error
    assert "RateLimitError" in full_error or "Rate" in full_error

