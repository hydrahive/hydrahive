"""
provider_config.py — Zentrale Provider-Konfiguration (v2)

Kapselt alle Provider-spezifischen Details an einem Ort:
- OAuth-Headers für Anthropic
- Identity-Block für Claude
- Provider-Erkennung aus Model-Namen

Statt diese Werte in 8+ Dateien zu hardcoden, wird alles von hier importiert.
Wenn OAuth stirbt oder der Provider wechselt → nur diese Datei ändern.
"""
from __future__ import annotations

import logging
import os

from .settings import settings

logger = logging.getLogger(__name__)


# =========================================================================
# Anthropic OAuth Konfiguration
# =========================================================================

# Headers die Anthropic für OAuth-Token-Requests erwartet.
# Werden nur genutzt wenn ein OAuth-Token (sk-ant-oat01-...) aktiv ist.
ANTHROPIC_OAUTH_HEADERS: dict[str, str] = {
    "anthropic-beta": "claude-code-20250219,oauth-2025-04-20,fine-grained-tool-streaming-2025-05-14,prompt-caching-2024-07-31",
    "user-agent": "claude-cli/2.1.62",
    "x-app": "cli",
}

# Identity-Block der als erster System-Block gesendet werden muss.
# Ohne diesen lehnt Anthropic OAuth-Requests mit 400 ab.
ANTHROPIC_OAUTH_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."


def get_oauth_system_blocks(system_prompt: str) -> list[dict]:
    """Baut die System-Blöcke für Anthropic OAuth: Identity + eigener Prompt.

    Anthropic OAuth erfordert den Identity-Block als erstes Element.
    Der eigene System-Prompt wird als zweites Element angehängt.
    """
    blocks = [
        {"type": "text", "text": ANTHROPIC_OAUTH_IDENTITY},
    ]
    if system_prompt:
        blocks.append({
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        })
    return blocks


# =========================================================================
# Provider-Erkennung
# =========================================================================

def detect_provider(model: str) -> str:
    """Erkennt den Provider aus dem Model-Namen.

    Returns: "anthropic", "openai", "google", "ollama", "deepseek", "mistral", "unknown"
    """
    m = model.lower()
    if any(x in m for x in ("claude", "anthropic")):
        return "anthropic"
    if any(x in m for x in ("gpt-", "gpt4", "o3", "o1", "openai", "chatgpt")):
        return "openai"
    if any(x in m for x in ("gemini", "google")):
        return "google"
    if any(x in m for x in ("ollama", "llama", "mistral", "qwen", "deepseek")):
        if "deepseek" in m:
            return "deepseek"
        if "mistral" in m:
            return "mistral"
        return "ollama"
    return "unknown"


def is_anthropic_model(model: str) -> bool:
    """True wenn das Modell ein Anthropic/Claude Modell ist."""
    return detect_provider(model) == "anthropic"


def is_oauth_token(token: str) -> bool:
    """True wenn der Token ein Anthropic OAuth-Token ist (sk-ant-oat01-...)."""
    return token.startswith("sk-ant-oat01-")


def needs_oauth_headers(model: str, api_key: str = "") -> bool:
    """True wenn OAuth-Headers gebraucht werden (Anthropic + OAuth-Token)."""
    if not is_anthropic_model(model):
        return False
    # Prüfe ob ein OAuth-Token konfiguriert ist
    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    return is_oauth_token(key)


# =========================================================================
# Prompt Caching — Provider-spezifisch
# =========================================================================

def supports_prompt_caching(model: str) -> bool:
    """True wenn der Provider Prompt-Caching unterstützt.

    - Anthropic: cache_control ephemeral (in Message-Blöcken)
    - OpenAI: automatisches Caching (kein explizites Flag nötig)
    - Google: Context Caching API (separat, nicht in Messages)
    - Ollama: kein Caching
    """
    provider = detect_provider(model)
    return provider in ("anthropic", "openai")


def apply_cache_control(messages: list[dict], model: str) -> list[dict]:
    """Wendet Provider-spezifisches Caching auf Messages an.

    Nur bei Anthropic werden cache_control-Blöcke hinzugefügt.
    Bei anderen Providern werden die Messages unverändert zurückgegeben —
    litellm/Provider handled Caching transparent.
    """
    if not is_anthropic_model(model):
        # Andere Provider: Messages unverändert, Caching passiert Provider-seitig
        return messages
    # Anthropic: cache_control auf System-Prompt und Tool-Definitions setzen
    # (wird in orchestrator_llm.py _apply_cache_control gemacht)
    return messages


# =========================================================================
# Provider-Platzhalter für zukünftige Integrationen
# =========================================================================

# OpenAI / ChatGPT Konfiguration
# TODO: Headers, Auth-Flow, Caching-Mechanismen wenn OpenAI-Support ausgebaut wird
OPENAI_CONFIG: dict = {
    "api_base": "https://api.openai.com/v1",
    "auth_header": "Authorization",        # "Bearer {api_key}"
    "caching": "automatic",                # OpenAI cached automatisch identische Prefixe
    "max_context": {
        "gpt-4o": 128_000,
        "gpt-4o-mini": 128_000,
        "o3": 200_000,
        "o3-mini": 200_000,
    },
}

# Google Gemini Konfiguration
# TODO: Context Caching API, Auth-Flow wenn Gemini-Support ausgebaut wird
GOOGLE_CONFIG: dict = {
    "api_base": "https://generativelanguage.googleapis.com/v1beta",
    "auth_header": "x-goog-api-key",
    "caching": "context_cache_api",        # Separates Caching-API, nicht in Messages
    "max_context": {
        "gemini-2.0-flash": 1_048_576,
        "gemini-2.5-pro": 1_048_576,
    },
}

# Ollama / Lokale Modelle
OLLAMA_CONFIG: dict = {
    "api_base": "http://localhost:11434",
    "auth_header": None,                   # Keine Auth für lokale Modelle
    "caching": "none",
    "max_context": {},                     # Modell-abhängig
}

# DeepSeek
DEEPSEEK_CONFIG: dict = {
    "api_base": "https://api.deepseek.com",
    "auth_header": "Authorization",
    "caching": "automatic",
    "max_context": {
        "deepseek-r1": 64_000,
    },
}
