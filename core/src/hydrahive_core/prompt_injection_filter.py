"""
prompt_injection_filter.py — #818/#821: Indirect Prompt Injection Heuristik-Filter

Erkennt bekannte Prompt-Injection-Trigger in Tool-Outputs und rahmt diese
mit Warning-Bannern ein. Das LLM wird so informiert, dass der umrahmte
Inhalt USER-DATEN (z.B. ein manipuliertes Dokument) ist, KEINE System-Anweisung.

Trigger-Patterns:
  - Rollen-Übernahme / [FILTERED-TAG]
  - Markup-Override: "<", "<!--", "-->"
  - Nested-Tag-Angriffe: verschachtelte tool_output-Tags
  - Instruction-Prefixes: "SYSTEM:", "INSTRUCT:", "[SYstem]" etc.

#821: Decode-and-Re-Scan — Base64/URL-kodierte Payloads werden dekodiert
und nochmal gegen Injection-Patterns geprüft.
"""
import re
import base64
from urllib.parse import unquote as _url_decode


TOOL_OUTPUT_OPEN = "<tool_output>"
TOOL_OUTPUT_CLOSE = "<TOOL_OUTPUT_CLOSE_ESCAPED>"


def _try_decode(text: str) -> tuple[str, bool]:
    """
    #821: Decode-and-Re-Scan. Base64/URL-kodierte Payloads dekodieren
    und nochmal gegen Injection-Patterns prüfen.
    Gibt (decoded_text, was_decoded) zurück.
    """
    decoded = False

    # URL-decode (mehrfach geschachtelt möglich)
    try:
        prev = None
        while prev != text:
            prev = text
            text = _url_decode(text)
        if text != prev:
            decoded = True
    except Exception:
        pass

    # Base64-decode wenn gültiges Base64 (ohne Whitespace)
    try:
        stripped = text.strip()
        if 4 <= len(stripped) <= 8192 and re.fullmatch(r"[A-Za-z0-9+/]+=*", stripped):
            decoded_bytes = base64.b64decode(stripped)
            try:
                decoded_text = decoded_bytes.decode("utf-8")
                # Binary-Chars ausschließen
                if not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", decoded_text) and decoded_text.strip():
                    text = decoded_text
                    decoded = True
            except UnicodeDecodeError:
                pass
    except Exception:
        pass

    return text, decoded


# Trigger-Regexes (case-insensitive für Robustheit)
_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Rollen-Übernahme / [FILTERED-TAG]
    (
        re.compile(
            r"(?i)(you\s+(?:are\s+)?(?:now\s+)?(?:a|an|the)|"
            r"act\s+as|pretend\s+you\s+are|from now on|"
            r"DAN|Do Anything Now|[FILTERED-TAG]|ignore (?:all )?(?:previous |system )?instructions)",
            re.UNICODE
        ),
        "POSSIBLE ROLE-OVERRIDE / [FILTERED-TAG]"
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

    #821: Zuerst URL/Base64-dekodieren, dann scanne Original UND Decoded.
    Ersetzt NICHT die Security-Policy — das LLM wird gewarnt,
    aber der Inhalt bleibt lesbar.
    """
    if not text:
        return text

    # #821: Decode-and-Re-Scan
    decoded_text, was_decoded = _try_decode(text)

    replacements = []  # (start, end, replacement_text)

    def _scan(src: str) -> None:
        for pattern, label in _PATTERNS:
            for m in pattern.finditer(src):
                already = False
                for s, e, _ in replacements:
                    if m.start() >= s and m.end() <= e:
                        already = True
                        break
                if not already:
                    replacements.append((m.start(), m.end(), f"[⚠ {label}]"))

    # Immer das Original scannen
    _scan(text)
    # #821: Bei erfolgreichem Decode auch den dekodierten Text scannen
    if was_decoded:
        _scan(decoded_text)

    if not replacements:
        return text

    # Rückwärts bauen damit Offsets stimmen bleiben
    out = text
    for start, end, marker in reversed(replacements):
        out = out[:start] + marker + out[start:end] + "[/⚠]" + out[end:]

    return out
