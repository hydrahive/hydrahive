"""
test_812_cors_config.py — CORS-Konfiguration darf nicht `*` + credentials kombinieren.

Issue #812: CORS mit allow_credentials=True und allow_origins=* wäre eine
CSRF/Cookie-Leak-Falle. Die main.py-Logik setzt credentials auf False wenn
'*' in der allow-list auftaucht. Dieser Test prüft den Code-Pfad
strukturell — ohne den FastAPI-App-Init laufen zu müssen.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


MAIN_PY = Path(__file__).parent.parent / "src" / "hydrahive_core" / "main.py"


def test_cors_wildcard_disables_credentials():
    """Die CORS-Middleware-Config muss bei '*' allow_credentials auf False setzen."""
    source = MAIN_PY.read_text(encoding="utf-8")

    # Block zwischen '#760 / #812' und dem nächsten Top-Level-Statement
    m = re.search(
        r"# #760 / #812: CORS.*?app\.add_middleware\(\s*CORSMiddleware,.*?\)",
        source, flags=re.DOTALL,
    )
    assert m, "CORS-Middleware-Block nicht gefunden — #812-Fix wurde entfernt?"
    block = m.group(0)

    # Guard: '*'-Branch existiert
    assert '"*" in _cors_origins' in block, \
        "Wildcard-Erkennung fehlt — #812 regrediert"
    # Guard: credentials wird im Wildcard-Fall auf False gesetzt
    assert "_cors_credentials = False" in block, \
        "allow_credentials wird bei '*' nicht deaktiviert — CSRF-Falle regrediert"
    # Guard: im Positiv-Fall (konkrete Origins) auf True
    assert "_cors_credentials = True" in block, \
        "allow_credentials-Branch für konkrete Origins fehlt"
    # Sanity: kein hardcoded allow_credentials=True mehr in der Middleware-Init
    assert "allow_credentials=True," not in block, \
        "allow_credentials=True hardcoded — #812 Fix rückgängig"


def test_cors_empty_origins_skips_middleware():
    """Bei leerer Origin-Liste wird gar keine CORS-Middleware registriert."""
    source = MAIN_PY.read_text(encoding="utf-8")
    m = re.search(
        r"_cors_origins = .*?\nif _cors_origins:",
        source, flags=re.DOTALL,
    )
    assert m, \
        "Empty-Origins-Fast-Path fehlt — same-origin wäre mit Wildcard-CORS unsicher"
