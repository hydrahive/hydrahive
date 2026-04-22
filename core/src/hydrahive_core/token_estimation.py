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
    if not text:
        return 0
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


def estimate_call_tokens(messages: list[dict], tools: list[dict] | None = None) -> int:
    """#778: Schaetzt Token-Kosten fuer einen LLM-Call inkl. multi-modal + tools.

    Zaehlt:
    - Text-Content aller Messages (auch in list[block]-Form).
    - Images pauschal 1000 Tokens pro Block (Anthropic Vision ist teurer,
      aber eine konservative Obergrenze ist fuer Budget-Checks erwuenscht).
    - Tools-Array als JSON-Serialisierung (so wie es an die API geht).
    """
    import json as _json
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype in ("image", "image_url"):
                    total += 1000
                elif btype == "text":
                    total += estimate_tokens(block.get("text", ""))
                else:
                    # tool_use, tool_result etc. — als JSON schaetzen
                    total += estimate_tokens(_json.dumps(block, ensure_ascii=False))
        else:
            total += estimate_tokens(str(content) if content else "")
        # JSON-Overhead pro Message
        total += 5
    if tools:
        total += estimate_tokens(_json.dumps(tools, ensure_ascii=False))
    return total
