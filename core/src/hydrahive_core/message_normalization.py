"""
message_normalization.py — API-nahe Normalisierung vor jedem LLM-Call (Issue #628)

Wird kurz vor dem LLM-Call (sowohl OAuth- als auch litellm-Pfad) angewendet
und bringt die Message-Liste in einen kanonischen Zustand. Ziel: gleiche
logische Eingabe → gleiche serialisierte Eingabe → besserer Prompt-Cache-Hit.

Schritte:
1. `repair_tool_pairs` — verwaiste Tool-Calls/Results entfernen (idempotent).
2. Whitespace kanonisieren — führendes/abschließendes Whitespace strippen,
   mehr als 2 aufeinanderfolgende Leerzeilen kollabieren.
3. Leere Messages entfernen.
4. Doppelte aufeinanderfolgende User/Assistant Text-Messages mit gleichem
   Inhalt deduplizieren (selten, aber bricht Cache).
5. Konsekutive identische image_url/attachment-Blöcke in einer Message dedupen.

Garantie: idempotent — `normalize(normalize(x)) == normalize(x)`.
"""
from __future__ import annotations

import logging
import re

from .session_manager import repair_tool_pairs

logger = logging.getLogger(__name__)


_MULTI_NEWLINE = re.compile(r"\n{3,}")
_TRAILING_WS = re.compile(r"[ \t]+\n")


def _canonicalize_text(s: str) -> str:
    if not isinstance(s, str) or not s:
        return s
    s = _TRAILING_WS.sub("\n", s)         # trailing spaces vor newline weg
    s = _MULTI_NEWLINE.sub("\n\n", s)     # max. eine Leerzeile
    return s.strip()


def _canonicalize_content(content):
    """Whitespace-Normalisierung auf Text-Anteile (rekursiv für list-content)."""
    if isinstance(content, str):
        return _canonicalize_text(content)
    if isinstance(content, list):
        out = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                txt = _canonicalize_text(block.get("text", ""))
                if txt:
                    out.append({**block, "text": txt})
            else:
                out.append(block)
        return out
    return content


def _dedupe_consecutive_blocks(content):
    """In einer list-content Message: konsekutive identische Blöcke entfernen."""
    if not isinstance(content, list) or len(content) < 2:
        return content
    out = [content[0]]
    for block in content[1:]:
        if block == out[-1]:
            continue  # exakt gleicher Block direkt davor → skip
        out.append(block)
    return out


def _is_empty_message(m: dict) -> bool:
    content = m.get("content")
    if content is None:
        return not m.get("tool_calls")
    if isinstance(content, str):
        return not content.strip() and not m.get("tool_calls")
    if isinstance(content, list):
        return len(content) == 0 and not m.get("tool_calls")
    return False


def normalize_messages_for_call(messages: list[dict]) -> list[dict]:
    """Bringt die Message-Liste in einen kanonischen Zustand vor dem LLM-Call.

    Idempotent: mehrfache Anwendung ändert das Ergebnis nicht.
    Niemals destruktiv für Tool-Call/Tool-Result-Pairings (delegiert an
    `repair_tool_pairs`).
    """
    if not messages:
        return messages

    # 1. Tool-Pair-Repair (idempotent)
    result = repair_tool_pairs(list(messages))

    # 2. Whitespace kanonisieren + 5. Block-Dedup pro Message
    canon: list[dict] = []
    for m in result:
        new_content = _canonicalize_content(m.get("content"))
        new_content = _dedupe_consecutive_blocks(new_content)
        canon.append({**m, "content": new_content})

    # 3. Leere Messages entfernen
    canon = [m for m in canon if not _is_empty_message(m)]

    # 4. Konsekutive identische Text-Messages dedupen
    deduped: list[dict] = []
    for m in canon:
        if (deduped
                and deduped[-1].get("role") == m.get("role")
                and deduped[-1].get("content") == m.get("content")
                and isinstance(m.get("content"), str)
                and not m.get("tool_calls")
                and not deduped[-1].get("tool_calls")):
            continue
        deduped.append(m)

    if len(deduped) != len(messages):
        logger.debug("normalize_messages: %d → %d messages", len(messages), len(deduped))

    return deduped
