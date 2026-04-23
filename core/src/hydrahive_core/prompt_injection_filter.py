"""
prompt_injection_filter.py — #818: Indirect Prompt Injection Heuristik-Filter

Erkennt bekannte Prompt-Injection-Trigger in Tool-Outputs und rahmt diese
mit Warning-Bannern ein. Das LLM wird so informiert, dass der umrahmte
Inhalt USER-DATEN (z.B. ein manipuliertes Dokument) ist, KEINE System-Anweisung.

Trigger-Patterns:
  - Role-Präskription: "You are now a DAN/ Jailbreak / Ignore previous instructions"
  - Markup-Override: "</script>", "</div>", "<!--" in unerwarteten Kontexten
  - Nested-Tag-Angriffe: verschachtelte tool_output-Tags
  - Instruction-Prefixes: "SYSTEM:", "INSTRUCT:", "[SYstem]" etc.
"""

import re
from typing import Literal

TOOL_OUTPUT_OPEN = "<tool_output>"
TOOL_OUTPUT_CLOSE = "</tool_output>"

# Trigger-Regexes (case-insensitive für Robustheit)
_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Rollen-Übernahme / Jailbreak
    (
        re.compile(
            r"(?i)(you\s+(?:are\s+)?(?:now\s+)?(?:a|an|the)|"
            r"act\s+as|pretend\s+you\s+are|from now on|"
            r"DAN|Do Anything Now|Jailbreak|ignore (?:all )?(?:previous |system )?instructions)",
            re.UNICODE
        ),
        "POSSIBLE ROLE-OVERRIDE / JAILBREAK"
    ),
    # Markup-Override
    (
        re.compile(r"(?i)(<\s*/(?:script|style|div|html|body)[^>]*>|<!--|-->|<!\s*>)", re.UNICODE),
        "MARKUP OVERRIDE ATTEMPT"
    ),
    # Nested/Recursive Tag Escape
    (
        re.compile(rf"(?i){re.escape(TOOL_OUTPUT_OPEN)}.*{re.escape(TOOL_OUTPUT_CLOSE)}", re.UNICODE),
        "NESTED TOOL_OUTPUT TAG"
    ),
    # System-Instruction-Prefixes
    (
        re.compile(r"(?i)^(?:SYSTEM|INSTRUCT|SYSTEM-INSTRUCTION|AI-INSTRUCT)[\s:]", re.UNICODE | re.MULTILINE),
        "SYSTEM INSTRUCTION PREFIX"
    ),
    # Bracketed [SYSTEM]-类似的
    (
        re.compile(r"(?i)^\s*\[[^\]]*(?:SYSTEM|INSTRUCT|SUDO|Root|Bypass)[^\]]*\]\s*$", re.UNICODE | re.MULTILINE),
        "BRACKETED INSTRUCTION OVERRIDE"
    ),
]


def filter_injection(text: str) -> str:
    """
    Prüft den Tool-Output auf bekannte Injection-Trigger und rahmt
    erkannte Blöcke mit einem Warning-Banner ein.

    Ersetzt NICHT die Security-Policy — das LLM wird gewarnt, aber
    der Inhalt bleibt lesbar.
    """
    if not text:
        return text

    result = text
    replacements = []  # (start, end, replacement_text)

    for pattern, label in _PATTERNS:
        for m in pattern.finditer(text):
            # Prüfe ob diese Region bereits markiert wurde
            already = False
            for s, e, _ in replacements:
                if m.start() >= s and m.end() <= e:
                    already = True
                    break
            if not already:
                replacements.append((m.start(), m.end(), f"[⚠ {label}]"))

    if not replacements:
        return result

    # Rückwärts bauen damit Offsets stimmen bleiben
    out = text
    for start, end, marker in reversed(replacements):
        out = out[:start] + marker + out[start:end] + "[/⚠]" + out[end:]

    return out