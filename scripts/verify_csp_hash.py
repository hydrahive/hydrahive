#!/usr/bin/env python3
"""
verify_csp_hash.py — stellt sicher, dass der CSP-script-src-Hash in
installer/hydrahive-security-headers.conf zum Inline-Script-Inhalt in
console/dist/index.html passt (#751).

Muss nach `npm run build` laufen. CI-rot = entweder index.html wurde
geändert (dann Hash in conf anpassen) oder Vite hat Whitespace-Output
angepasst (dann Build-Änderung untersuchen).
"""
from __future__ import annotations

import base64
import hashlib
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
HTML = REPO / "console" / "dist" / "index.html"
CONF = REPO / "installer" / "hydrahive-security-headers.conf"


def inline_script_hash(html: str) -> str:
    m = re.search(r"<script>(.*?)</script>", html, re.DOTALL)
    if not m:
        raise SystemExit("no inline <script> in dist/index.html — unerwartet")
    content = m.group(1)
    digest = hashlib.sha256(content.encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


def csp_script_hash(conf: str) -> str:
    m = re.search(r"script-src[^;]*'sha256-([A-Za-z0-9+/=]+)'", conf)
    if not m:
        raise SystemExit("kein sha256-Hash in script-src der CSP-conf gefunden")
    return m.group(1)


def main() -> int:
    if not HTML.exists():
        print(f"skip: {HTML} fehlt — erst `npm run build` in console/ laufen lassen.")
        return 0
    built = inline_script_hash(HTML.read_text(encoding="utf-8"))
    configured = csp_script_hash(CONF.read_text(encoding="utf-8"))
    if built == configured:
        print(f"CSP-Hash passt: sha256-{built}")
        return 0
    print("CSP-Hash-Drift erkannt!", file=sys.stderr)
    print(f"  dist/index.html → sha256-{built}", file=sys.stderr)
    print(f"  nginx-conf      → sha256-{configured}", file=sys.stderr)
    print("", file=sys.stderr)
    print("Fix:  Hash in installer/hydrahive-security-headers.conf auf den", file=sys.stderr)
    print("      neuen Wert aktualisieren — ODER prüfen ob die Script-Änderung", file=sys.stderr)
    print("      in console/index.html absichtlich ist.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
