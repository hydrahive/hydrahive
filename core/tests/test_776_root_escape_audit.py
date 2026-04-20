"""Test #776 UNRESTRICTED-Root-Escape: error-Log + audit_log Wiring.

Die eigentliche shell_exec-Execution laesst sich ohne volle Env nicht in
Unit-Tests durchlaufen — stattdessen pruefen wir die statische Struktur:
welche Log-Level und Audit-Hooks sind an den richtigen Stellen.
"""
from __future__ import annotations

import inspect

from hydrahive_core import tool_registry as _tr


def _shell_exec_source() -> str:
    """Extract the shell_exec dispatch code from ShellExecTool."""
    # Die execute-Methode ist nicht statisch lesbar (sie lebt innerhalb einer
    # Factory); wir lesen das Modul und suchen den Block.
    return inspect.getsource(_tr)


def test_root_escape_uses_error_level():
    """#776: Der ROOT-Escape-Branch muss auf logger.error loggen, nicht warning."""
    src = _shell_exec_source()
    # Finde den Escape-Branch: "UNRESTRICTED/ROOT via Env-Override"
    idx = src.find("UNRESTRICTED/ROOT via Env-Override")
    assert idx > 0, "Escape-Branch-Message nicht gefunden"
    # Der Log-Call muss kurz davor oder drumherum als logger.error auftauchen
    window = src[max(0, idx - 200):idx + 200]
    assert "logger.error" in window, f"Erwarte logger.error in Escape-Branch; window={window!r}"


def test_root_escape_calls_audit_log():
    """#776: Der ROOT-Escape-Branch muss _audit_log_fn aufrufen (Audit-Trail)."""
    src = _shell_exec_source()
    idx = src.find("UNRESTRICTED/ROOT via Env-Override")
    assert idx > 0
    # Audit-Call folgt nach dem Log (innerhalb der naechsten ~500 Zeichen)
    window = src[idx:idx + 700]
    assert "_audit_log_fn" in window
    assert "shell_exec_root_override" in window


def test_audit_log_fn_is_wired_in_main():
    """#776: main.py muss _tr._audit_log_fn = audit_log zuweisen."""
    from hydrahive_core import main as _main
    src = inspect.getsource(_main)
    assert "_audit_log_fn = audit_log" in src


def test_startup_check_present_in_lifespan():
    """#776: lifespan() muss beim Core-Start HYDRAHIVE_UNRESTRICTED_ALLOW_ROOT
    detektieren + ERROR-Log ausgeben."""
    from hydrahive_core import main as _main
    src = inspect.getsource(_main.lifespan)
    assert "HYDRAHIVE_UNRESTRICTED_ALLOW_ROOT" in src
    assert "AUDIT [SECURITY]" in src
    assert "logger.error" in src


def test_audit_log_signature_matches_use():
    """Cross-check: audit_log Signatur passt zu dem Aufruf im tool_registry."""
    from hydrahive_core import main as _main
    sig = inspect.signature(_main.audit_log)
    params = sig.parameters
    # Der Aufruf nutzt: action, user, target, project_id, details
    for required in ("action", "user", "target", "project_id", "details"):
        assert required in params, f"audit_log fehlt Parameter {required}"
