"""
frustration_detection.py — Erkennt User-Frustration in Nachrichten (#485)

Inspiriert von Claude Code userPromptKeywords.ts.
Regex-basierte Erkennung (DE + EN), gibt System-Prompt-Injection zurück.
"""
import re

# Deutsch + Englisch Patterns
_FRUSTRATION_PATTERN = re.compile(
    r"\b("
    # Englisch (aus Claude Code)
    r"wtf|wth|ffs|omfg|shit(?:ty|tiest)?|dumbass|horrible|awful|"
    r"piss(?:ed|ing)?\s*off|piece\s+of\s+(?:shit|crap|junk)|"
    r"what\s+the\s+(?:fuck|hell)|fuck(?:ing)?\s+(?:broken|useless|terrible|awful)|"
    r"fuck\s*you|screw\s+(?:this|you)|so\s+frustrating|this\s+sucks|damn\s*it|"
    # Deutsch
    r"scheiße|scheiß|verdammt|mist|kacke|"
    r"funktioniert\s+(?:nicht|nix|gar\s+nicht|nie)|"
    r"geht\s+(?:nicht|nix|gar\s+nicht)|"
    r"nervt|nervig|zum\s+kotzen|was\s+soll\s+der\s+mist|"
    r"so\s+ein\s+dreck|ey\s+alter|mann\s+ey|oh\s+man[n]?"
    r")\b",
    re.IGNORECASE,
)

_FRUSTRATION_INJECTION = (
    "\n\n[SYSTEM-HINWEIS: Der User scheint frustriert zu sein. "
    "Sei besonders sorgfältig, entschuldige dich kurz falls nötig, "
    "biete konkrete Lösungsschritte an, und vermeide lange Erklärungen. "
    "Fokus auf schnelle, praktische Hilfe.]"
)


def detect_frustration(text: str) -> bool:
    """Prüft ob der Text Frustrations-Indikatoren enthält."""
    return bool(_FRUSTRATION_PATTERN.search(text))


def get_frustration_injection(text: str) -> str:
    """Gibt System-Prompt-Injection zurück wenn Frustration erkannt, sonst leer."""
    if detect_frustration(text):
        return _FRUSTRATION_INJECTION
    return ""
