"""
token_estimation.py — Zentrale Token-Schätzung (#387)

Eine Quelle für Token-Estimation statt 3 verschiedene Implementierungen.
Wird von orchestrator.py, orchestrator_context.py und session_manager.py genutzt.
"""
from __future__ import annotations


def estimate_tokens(text: str) -> int:
    """Token-Schätzung: chars / 3.2 (leicht konservativ, ~10% Sicherheit).

    Genauer als chars/4 (zu optimistisch) für gemischten Content
    (Code, Deutsch, JSON, Markdown).
    """
    return max(1, int(len(text) / 3.2))


def estimate_message_tokens(message: dict) -> int:
    """Token-Schätzung für eine LLM-Message (inkl. JSON-Overhead)."""
    content = message.get("content", "")
    if not isinstance(content, str):
        content = str(content) if content else ""
    base = estimate_tokens(content)
    # JSON-Overhead: role, formatting etc.
    overhead = 5
    if message.get("tool_call_id"):
        overhead += 15
    return base + overhead


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Token-Schätzung für eine Liste von Messages."""
    return sum(estimate_message_tokens(m) for m in messages)
