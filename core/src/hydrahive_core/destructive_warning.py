"""
destructive_warning.py — Warnung bei potenziell destruktiven Befehlen (#486)

Inspiriert von Claude Code destructiveCommandWarning.ts.
Pattern-basierte Erkennung, rein informativ (blockiert nichts).
"""
import re

_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Git — Datenverlust / schwer umkehrbar
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "Kann uncommitted Änderungen verwerfen"),
    (re.compile(r"\bgit\s+push\b[^;&|\n]*(?:--force|-f)\b"), "Kann Remote-History überschreiben"),
    (re.compile(r"\bgit\s+clean\b[^;&|\n]*-[a-zA-Z]*f"), "Kann untracked Dateien dauerhaft löschen"),
    (re.compile(r"\bgit\s+checkout\s+(?:--\s+)?\."), "Kann alle Working-Tree-Änderungen verwerfen"),
    (re.compile(r"\bgit\s+restore\s+(?:--\s+)?\."), "Kann alle Working-Tree-Änderungen verwerfen"),
    (re.compile(r"\bgit\s+stash\s+(?:drop|clear)\b"), "Kann gestashte Änderungen dauerhaft entfernen"),
    (re.compile(r"\bgit\s+branch\s+-D\b"), "Force-Delete eines Branches"),
    # Git — Safety-Bypass
    (re.compile(r"\bgit\s+(?:commit|push|merge)\b[^;&|\n]*--no-verify\b"), "Überspringt Safety-Hooks"),
    (re.compile(r"\bgit\s+commit\b[^;&|\n]*--amend\b"), "Überschreibt den letzten Commit"),
    # Datei-Löschung
    (re.compile(r"(?:^|[;&|\n]\s*)rm\s+-[a-zA-Z]*[rR][a-zA-Z]*f"), "Rekursives Force-Delete"),
    (re.compile(r"(?:^|[;&|\n]\s*)rm\s+-[a-zA-Z]*[rR]"), "Rekursives Löschen"),
    # Datenbank
    (re.compile(r"\b(?:DROP|TRUNCATE)\s+(?:TABLE|DATABASE|SCHEMA)\b", re.I), "Kann Datenbank-Objekte löschen"),
    (re.compile(r"\bDELETE\s+FROM\s+\w+\s*(?:;|$)", re.I), "Kann alle Zeilen einer Tabelle löschen"),
    # Infrastruktur
    (re.compile(r"\bkubectl\s+delete\b"), "Kann Kubernetes-Ressourcen löschen"),
    (re.compile(r"\bterraform\s+destroy\b"), "Kann Terraform-Infrastruktur zerstören"),
    (re.compile(r"\bdocker\s+(?:rm|rmi|system\s+prune)\b"), "Kann Docker-Container/Images löschen"),
    (re.compile(r"\bsystemctl\s+(?:stop|disable)\b"), "Kann Services stoppen"),
]


def get_destructive_warning(command: str) -> str | None:
    """Prüft ob ein Befehl ein bekanntes destruktives Pattern matcht.
    Gibt Warnung zurück oder None."""
    for pattern, warning in _PATTERNS:
        if pattern.search(command):
            return warning
    return None
