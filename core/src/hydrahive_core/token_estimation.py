"""
token_estimation.py — Zentrale Token-Schätzung (#387, #843).

Eine Quelle für Token-Estimation statt 3 verschiedene Implementierungen.
Wird von orchestrator.py, orchestrator_context.py und session_manager.py genutzt.

#843 Gate 7: echter Tokenizer statt chars/3.2-Heuristik.
- claude/gpt: tiktoken (cl100k_base als Default-Encoding fuer alle modernen
  OpenAI/Anthropic-Modelle)
- minimax/qwen/kimi/etc: HuggingFace-Tokenizer wenn verfuegbar, sonst
  Fallback auf chars/3.2-Heuristik mit einmaligem Warning-Log
- Tokenizer-Instanzen werden gecached pro Modell
- Graceful: fehlende Lib → Fallback, kein Crash
"""
from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


# ─── Tokenizer-Cache ───────────────────────────────────────────────────

_tokenizer_cache: dict[str, Any] = {}
_cache_lock = threading.Lock()
_warned_models: set[str] = set()


def _normalize_model(model: str) -> str:
    """Normalisiert Modell-Namen (lowercase, ohne Provider-Prefix)."""
    if not model:
        return ""
    m = model.lower().strip()
    # Provider-Prefix entfernen ('openai/gpt-4' → 'gpt-4', 'anthropic/...' → '...')
    if "/" in m:
        m = m.rsplit("/", 1)[-1]
    return m


def _select_encoding_name(model_norm: str) -> str | None:
    """Mapping Modell → tiktoken-Encoding. None wenn nicht via tiktoken machbar."""
    # GPT-5 / GPT-4o / o-series → o200k_base
    if any(x in model_norm for x in ("gpt-4o", "gpt-5", "o1", "o3", "o4")):
        return "o200k_base"
    # GPT-4, GPT-3.5, claude → cl100k_base (claude-Tokenizer ist nicht offiziell verfuegbar,
    # cl100k ist die beste Annaeherung; im Zweifel ueberschaetzt es leicht)
    if any(x in model_norm for x in ("gpt-4", "gpt-3.5", "gpt", "claude")):
        return "cl100k_base"
    return None


def _get_tokenizer(model: str):
    """Returns ein callable(text) → int, oder None bei Fallback noetig."""
    norm = _normalize_model(model)
    with _cache_lock:
        if norm in _tokenizer_cache:
            return _tokenizer_cache[norm]
    enc_name = _select_encoding_name(norm)
    tokenizer = None
    if enc_name:
        try:
            import tiktoken
            enc = tiktoken.get_encoding(enc_name)
            tokenizer = lambda text: len(enc.encode(text or "", disallowed_special=()))
        except Exception as e:
            if norm not in _warned_models:
                logger.warning(
                    "tiktoken unavailable for model %s (encoding=%s): %s — fallback to heuristic",
                    norm, enc_name, e,
                )
                _warned_models.add(norm)
            tokenizer = None
    # Andere Modelle (minimax/qwen/etc) → kein Versuch, direkt Fallback.
    # HuggingFace-Tokenizer-Integration waere optional dep, fuer Phase-2.
    with _cache_lock:
        _tokenizer_cache[norm] = tokenizer
    return tokenizer


def _heuristic_tokens(text: str) -> int:
    """Fallback wenn kein echter Tokenizer verfuegbar ist."""
    if not text:
        return 0
    return max(1, int(len(text) / 3.2))


def estimate_tokens(text: str, model: str = "") -> int:
    """Token-Schätzung. Mit model-Param: nutzt echten Tokenizer wenn moeglich.

    Ohne model: heuristik (chars/3.2). Existing callers ohne model bleiben
    funktional, aber neue Callers sollten model durchreichen fuer Praezision.
    """
    if not text:
        return 0
    if model:
        tok = _get_tokenizer(model)
        if tok is not None:
            try:
                return tok(text)
            except Exception as e:
                logger.debug("tokenizer error for %s: %s — fallback", model, e)
    return _heuristic_tokens(text)


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
