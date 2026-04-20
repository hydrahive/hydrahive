"""
Invariante #771: Telegram-Handler lädt cfg pro Message frisch aus Disk.

Hintergrund: `_handle_message` ist eine nested-function innerhalb von
`start_telegram_bot` und schließt über den `cfg`-Parameter. Ohne Reload
würden Updates via `PUT /me/telegram/config` (Admin-IDs, Filter) erst
nach Bot-Restart greifen. Der Handler muss `load_telegram_config(username)`
am Anfang aufrufen, damit `cfg.get(...)` downstream Live-Werte liest.

Dies ist ein Architektur-Test (AST-level), weil `_handle_message` als
Closure nicht direkt aufrufbar ist.
"""
from __future__ import annotations

import ast
from pathlib import Path


SRC = Path(__file__).parent.parent / "src" / "hydrahive_core" / "telegram_agent.py"


def test_handle_message_reloads_config_from_disk():
    tree = ast.parse(SRC.read_text())

    handler = None
    for outer in ast.walk(tree):
        if isinstance(outer, ast.AsyncFunctionDef) and outer.name == "start_telegram_bot":
            for inner in ast.walk(outer):
                if isinstance(inner, ast.AsyncFunctionDef) and inner.name == "_handle_message":
                    handler = inner
                    break
    assert handler is not None, "_handle_message nicht in start_telegram_bot gefunden"

    calls = [
        n for n in ast.walk(handler)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "load_telegram_config"
    ]
    assert calls, (
        "_handle_message muss cfg pro Message via load_telegram_config(username) "
        "frisch laden (#771) — sonst greifen Config-Updates erst nach Bot-Restart."
    )
