"""Validation helpers for LLM provider configuration values."""
from __future__ import annotations


class LlmConfigValueError(ValueError):
    """Raised when a provider config value is unsafe for API transport."""


def clean_provider_secret(value: str | None, *, label: str = "api_key") -> str:
    """Return a stripped API secret that is safe to pass as an HTTP header.

    OpenAI-compatible clients forward API keys as headers. Header values must
    stay ASCII and single-line; otherwise httpx/openai fail with codec errors.
    """
    cleaned = (value or "").strip()
    if not cleaned:
        return ""
    if "\n" in cleaned or "\r" in cleaned:
        raise LlmConfigValueError(
            f"{label} darf keine Zeilenumbrueche enthalten. Bitte nur den reinen API-Key einfuegen."
        )
    try:
        cleaned.encode("ascii")
    except UnicodeEncodeError as exc:
        raise LlmConfigValueError(
            f"{label} darf nur ASCII-Zeichen enthalten. Bitte nur den reinen API-Key einfuegen."
        ) from exc
    return cleaned


def clean_provider_base_url(value: str | None, *, label: str = "base_url") -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        return ""
    if any(ch.isspace() for ch in cleaned):
        raise LlmConfigValueError(f"{label} darf keine Leerzeichen oder Zeilenumbrueche enthalten.")
    try:
        cleaned.encode("ascii")
    except UnicodeEncodeError as exc:
        raise LlmConfigValueError(f"{label} darf nur ASCII-Zeichen enthalten.") from exc
    return cleaned
