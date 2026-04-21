"""minimax_invoke_parser.py — XML-Invoke-Markup als Tool-Call interpretieren (#792).

MiniMax-M2.7 schreibt gelegentlich Anthropic-Style XML-Markup für
Tool-Aufrufe als plain Text in den Stream statt strukturierte
``tool_use``-Blöcke zu senden:

.. code-block:: text

   <invoke name="image_generate">
     <parameter name="prompt">A hamster smoking a pipe</parameter>
     <parameter name="aspect_ratio">1:1</parameter>
   </invoke>

Dieser Parser extrahiert solche Blöcke und baut synthetische
:class:`SyntheticToolUse` für den Dispatch-Loop. Caller muss sicherstellen
dass der Parser **nur für MiniMax-Modelle** aktiv wird — andere Models
(Claude Original, GPT, Codex) könnten legitime Erklär-Texte mit
XML-Beispielen enthalten, die fälschlich als Tool-Call interpretiert
würden.
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SyntheticToolUse:
    """Leichtgewichtiger Duck-Typ für ``anthropic.types.ToolUseBlock``.

    Der Stream-Loop greift auf ``.name``, ``.input``, ``.id``, ``.type`` zu
    (Attribute, keine dict-Keys). Diese Klasse matched diese API.
    """
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)
    type: str = "tool_use"


_INVOKE_RE = re.compile(
    r'<invoke\s+name="([^"]+?)"[^>]*>'
    r'(.*?)'
    r'</invoke>',
    re.DOTALL | re.IGNORECASE,
)

_PARAM_RE = re.compile(
    r'<parameter\s+name="([^"]+?)"[^>]*>'
    r'(.*?)'
    r'</parameter>',
    re.DOTALL | re.IGNORECASE,
)


def _generate_id() -> str:
    return f"mm_xml_{secrets.token_hex(4)}"


def parse_invoke_markup(text: str | None) -> list[SyntheticToolUse]:
    """Extrahiert alle ``<invoke>``-Blöcke aus ``text``.

    Unterstützt:
      * Ein oder mehrere Blöcke
      * Parameter-Werte mit Newlines
      * Unescaped Quotes innerhalb von Werten (non-greedy Matching
        bis ``</parameter>``)
      * Case-insensitive Tags (``<INVOKE>``, ``<Parameter>``)

    Gibt leere Liste zurück bei:
      * ``text`` None oder leer
      * Kein ``<invoke`` im Text
      * Nur partial/offener Tag
      * Unparsebare Inputs (defensiv)
    """
    if not text or "<invoke" not in text.lower():
        return []

    blocks: list[SyntheticToolUse] = []
    for match in _INVOKE_RE.finditer(text):
        tool_name = match.group(1).strip()
        if not tool_name:
            continue
        inner = match.group(2)
        params: dict[str, Any] = {}
        for p_match in _PARAM_RE.finditer(inner):
            param_name = p_match.group(1).strip()
            param_value = p_match.group(2).strip()
            if param_name:
                params[param_name] = param_value
        blocks.append(SyntheticToolUse(
            id=_generate_id(),
            name=tool_name,
            input=params,
        ))
    return blocks


def text_without_invokes(text: str | None) -> str:
    """Entfernt alle ``<invoke>``-Blöcke aus ``text``, für saubere Session-Logs.

    None → leerer String (konsistent, damit Caller keinen None-Check braucht).
    """
    if not text:
        return ""
    return _INVOKE_RE.sub("", text)
