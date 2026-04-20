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


# =========================================================================
# OpenAI → Anthropic Format Converter (#637-Followup)
# =========================================================================
# Wird vor JEDEM Anthropic-Provider-Send aufgerufen, damit Anthropic nie rohe
# `role: "tool"`-Messages oder OpenAI-`tool_calls`-Felder sieht.
# Vorher dupliziert in _anthropic_oauth_call und _stream_anthropic_oauth —
# ein Helper, eine Wahrheit.

def to_anthropic_format(messages: list[dict]) -> tuple[str, list[dict]]:
    """Konvertiert OpenAI-Format-Messages in Anthropic-Format.

    Input (OpenAI):
        [{role: system|user|assistant|tool, content, tool_calls?, tool_call_id?}, ...]

    Output (Anthropic):
        (system_msg_str, [{role: user|assistant, content: str | [blocks]}, ...])

    Konvertierung:
        - role: "system" → in system_msg-String extrahiert (kein Anthropic-message)
        - role: "tool" mit tool_call_id → user-Message mit `tool_result`-Block;
          mit vorherigem user-Block-List zusammengelegt
        - role: "assistant" mit tool_calls → assistant-Message mit text + tool_use-Blöcken
        - sonstige Text-Messages → durchgereicht (role/content)

    Plus consecutive same-role Text-Messages werden gemerged (Anthropic erfordert
    user/assistant-Wechsel). Messages mit Block-Listen werden NICHT gemerged.
    """
    import json as _json

    system_parts: list[str] = []
    out: list[dict] = []
    for m in messages:
        role = m.get("role", "")

        if role == "system":
            sc = m.get("content", "")
            if isinstance(sc, str) and sc:
                system_parts.append(sc)
            elif isinstance(sc, list):
                # System-Content kann schon Block-Liste sein (z.B. von _apply_cache_control)
                for b in sc:
                    if isinstance(b, dict) and b.get("type") == "text":
                        system_parts.append(b.get("text", ""))
            continue

        if role == "tool":
            tool_result_block = {
                "type":        "tool_result",
                "tool_use_id": m.get("tool_call_id", "unknown"),
                "content":     m.get("content", "") or "",
            }
            # An vorherige user-Block-Liste anhängen, sonst neuen user-Eintrag
            if out and out[-1]["role"] == "user" and isinstance(out[-1].get("content"), list):
                out[-1]["content"].append(tool_result_block)
            else:
                out.append({"role": "user", "content": [tool_result_block]})
            continue

        tool_calls = m.get("tool_calls")
        if role == "assistant" and tool_calls:
            asst_content: list[dict] = []
            text = m.get("content")
            if text:
                asst_content.append({"type": "text", "text": text})
            for tc in tool_calls:
                fn = tc.get("function", {})
                try:
                    inp = _json.loads(fn.get("arguments", "{}"))
                except Exception:
                    inp = {}
                asst_content.append({
                    "type":  "tool_use",
                    "id":    tc.get("id", "unknown"),
                    "name":  fn.get("name", "unknown"),
                    "input": inp,
                })
            out.append({"role": "assistant", "content": asst_content})
            continue

        # Normale Text-Message — Content kann String oder bereits Block-Liste sein
        out.append({"role": role, "content": m.get("content") or ""})

    # Consecutive same-role Text-Messages mergen (Anthropic-Constraint).
    # Block-Listen-Messages bleiben unverändert (würden sonst tool_use/tool_result
    # zerstören).
    merged: list[dict] = []
    for m in out:
        if (merged
                and merged[-1]["role"] == m["role"]
                and isinstance(m.get("content"), str)
                and isinstance(merged[-1].get("content"), str)):
            merged[-1] = {**merged[-1],
                          "content": merged[-1]["content"] + "\n\n" + m["content"]}
        else:
            merged.append(dict(m))

    # Anthropic verlangt: auf eine assistant-Message mit tool_use muss in der
    # NÄCHSTEN Message ein user-Block mit den korrespondierenden tool_result-
    # Blöcken folgen. Alt-/Resume-History kann hier trotz OpenAI-seitigem
    # Repair noch exotische Lücken haben. Als letzte Sicherheitsstufe heilen
    # wir die Anthropic-Format-History direkt vor dem Send.
    repaired: list[dict] = []
    for idx, m in enumerate(merged):
        repaired.append(dict(m))
        if m.get("role") != "assistant" or not isinstance(m.get("content"), list):
            continue
        tool_use_ids = [
            str(b.get("id", "")).strip()
            for b in m["content"]
            if isinstance(b, dict) and b.get("type") == "tool_use" and str(b.get("id", "")).strip()
        ]
        if not tool_use_ids:
            continue

        next_msg = merged[idx + 1] if idx + 1 < len(merged) else None
        next_tool_results: set[str] = set()
        if next_msg and next_msg.get("role") == "user" and isinstance(next_msg.get("content"), list):
            next_tool_results = {
                str(b.get("tool_use_id", "")).strip()
                for b in next_msg["content"]
                if isinstance(b, dict) and b.get("type") == "tool_result"
            }

        missing = [tcid for tcid in tool_use_ids if tcid not in next_tool_results]
        if not missing:
            continue

        logger.warning(
            "to_anthropic_format: ergänze %d fehlende tool_result-Blöcke direkt nach tool_use (%s)",
            len(missing), ", ".join(missing[:3]),
        )
        repaired.append({
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tcid,
                    "content": "[Session unterbrochen — Ergebnis nicht verfügbar]",
                }
                for tcid in missing
            ],
        })

    system_msg = "\n\n".join(p for p in system_parts if p)
    return system_msg, repaired


# =========================================================================
# Anthropic → OpenAI Content-Block Converter (BL-16)
# =========================================================================
# MiniMax via LiteLLM nutzt OpenAI-Chat-Completions (MinimaxChatConfig).
# LiteLLM's validate_chat_completion_user_messages() akzeptiert nur OpenAI-
# Block-Typen (text, image_url, input_audio, ...). Anthropic-Image-Blocks
# {"type": "image", "source": {...}} werden rejected → "invalid content
# type=image". Konvertierung wird NUR im MiniMax-Pfad aufgerufen — Claude-
# OAuth und Claude-API-direct laufen weiterhin ueber to_anthropic_format
# / das Anthropic SDK und brauchen die Konvertierung nicht.

def to_openai_message_content(content):
    """Konvertiert Anthropic-Image-Blocks zu OpenAI image_url-Blocks.

    Nicht-image-Blocks (text, tool_use, tool_result, ...) werden 1:1
    durchgereicht — die Funktion ist damit idempotent bei Mehrfach-Aufruf.
    String-Content bleibt unveraendert.
    """
    if not isinstance(content, list):
        return content
    result = []
    for block in content:
        if not isinstance(block, dict):
            result.append(block)
            continue
        if block.get("type") == "image":
            source = block.get("source") or {}
            data = source.get("data", "")
            media_type = source.get("media_type") or "image/png"
            result.append({
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{data}"},
            })
        else:
            result.append(block)
    return result


def convert_anthropic_images_to_openai(messages: list[dict]) -> list[dict]:
    """Konvertiert Anthropic-Image-Blocks in allen User-Messages zu OpenAI-Format.

    Nur User-Rolle — LiteLLM's Validator prueft explizit User-Messages.
    Assistant/System/Tool-Messages haben andere Block-Strukturen und werden
    nicht durch die Validation gefiltert.
    """
    result = []
    for m in messages:
        if m.get("role") == "user" and isinstance(m.get("content"), list):
            m = {**m, "content": to_openai_message_content(m["content"])}
        result.append(m)
    return result
