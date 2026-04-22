"""
test_786_policy_dump.py — context_lifecycle Policy-Matrix dump.

Stellt sicher dass dump_effective_policies_markdown() ein vollstaendiges
Markdown-Dokument liefert (alle drei Tabellen + Memory-Budget-Zeile) und
dass log_effective_policies_once() einen INFO-Log produziert.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_dump_contains_all_sections():
    from hydrahive_core.context_lifecycle import dump_effective_policies_markdown
    md = dump_effective_policies_markdown()
    assert "## Tool-Op-Type Mapping" in md
    assert "## Detail-Policies" in md
    assert "## Result-Budgets pro Op-Type" in md
    # Mindestens ein bekanntes Tool aus jeder Kategorie
    assert "`shell_exec`" in md
    assert "`file_read`" in md
    assert "`web_search`" in md
    # Op-Types
    assert "`mutation`" in md
    assert "`read`" in md
    assert "`search`" in md
    assert "`meta`" in md
    # Memory-Budget-Zeile
    assert "Memory-Budgets" in md
    assert "normal=6000" in md


def test_log_effective_policies_emits_info(caplog):
    from hydrahive_core.context_lifecycle import log_effective_policies_once
    caplog.set_level(logging.INFO, logger="hydrahive_core.context_lifecycle")
    log_effective_policies_once()
    assert any(
        "context_lifecycle effective policies (#786)" in r.message
        for r in caplog.records
    ), "kein INFO-Log mit Policy-Dump"


def test_dump_lists_every_known_tool_policy():
    """Regression-Guard: wer eine neue Tool-Policy zu _TOOL_POLICIES hinzufuegt
    sollte sie automatisch im Dump sehen — Test pruefst dass die Anzahl
    der Detail-Eintraege == len(_TOOL_POLICIES) ist."""
    from hydrahive_core.context_lifecycle import (
        dump_effective_policies_markdown,
        _TOOL_POLICIES,
    )
    md = dump_effective_policies_markdown()
    # Detail-Tabelle: jede Zeile beginnt mit "| `<tool>` |"
    detail_section = md.split("## Detail-Policies", 1)[1].split("##", 1)[0]
    counted = sum(1 for line in detail_section.splitlines() if line.startswith("| `"))
    assert counted == len(_TOOL_POLICIES), \
        f"Detail-Tabelle hat {counted} Zeilen, _TOOL_POLICIES {len(_TOOL_POLICIES)}"
