"""
test_787_subagent_isolation_invariant.py — Catch-All-Category fuer Subagent-Isolation.

Aus Audit #787:
  - ToolCategory.UNKNOWN existiert + ist fail-closed in read_only/patch_only
    (war bereits implementiert).
  - Was fehlte: explizite Mappings fuer neuere Tools (image_generate,
    video_generate, music_generate, server_*, wks_*) damit sie nicht still
    in UNKNOWN landen + eine Invariante damit zukuenftige Tools sichtbar
    werden.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


# ─── ToolCategory.UNKNOWN als Fail-closed-Default ──────────────────────

def test_unknown_category_blocked_in_read_only():
    from hydrahive_core.subagent_isolation import allow_tool, IsolationMode
    decision = allow_tool(IsolationMode.READ_ONLY, "totally_unknown_tool_xyz")
    assert decision.allowed is False
    assert "unknown" in decision.reason.lower() or "unknown" in decision.reason


def test_unknown_category_blocked_in_patch_only():
    from hydrahive_core.subagent_isolation import allow_tool, IsolationMode
    decision = allow_tool(IsolationMode.PATCH_ONLY, "totally_unknown_tool_xyz")
    assert decision.allowed is False


def test_unknown_category_allowed_in_full_worktree():
    from hydrahive_core.subagent_isolation import allow_tool, IsolationMode
    decision = allow_tool(IsolationMode.FULL_WORKTREE, "totally_unknown_tool_xyz")
    assert decision.allowed is True


# ─── Neue Mappings ─────────────────────────────────────────────────────

@pytest.mark.parametrize("tool, expected_category", [
    # SHELL
    ("server_shell",     "shell"),
    ("wks_shell_exec",   "shell"),
    # WRITE — Media + Remote-File-Write
    ("server_file_write", "write"),
    ("image_generate",    "write"),
    ("video_generate",    "write"),
    ("music_generate",    "write"),
    # READ — Remote-File-Read
    ("server_file_read",  "read"),
])
def test_explicit_mapping(tool: str, expected_category: str):
    from hydrahive_core.subagent_isolation import tool_category
    assert tool_category(tool).value == expected_category


# ─── Invariante: Core-Tools haben Mapping ──────────────────────────────

# Core-Tools die in tool_registry.py als BaseTool-Subklassen leben.
# Plugin-/Gitea-/External-Tools sind ausgenommen (UNKNOWN→full_worktree=allow ist OK,
# read_only/patch_only blockt sie sowieso konservativ).
_CORE_TOOL_IDS_FROM_REGISTRY: list[str] = [
    "shell_exec", "file_read", "file_write", "file_patch", "file_search",
    "web_search", "read_memory", "write_memory", "ask_agent", "tool_search",
    "server_shell", "server_file_read", "server_file_write", "wks_shell_exec",
    "image_generate", "video_generate", "music_generate",
]


def test_invariant_every_core_tool_has_explicit_mapping():
    """Wenn ein neues Core-Tool dazukommt aber kein Mapping in
    TOOL_CATEGORIES bekommt, faellt es in UNKNOWN. In read_only/patch_only
    ist das fail-closed — aber der Admin sollte die Wahl bewusst treffen,
    nicht via Default. Dieser Test zwingt zum Mapping."""
    from hydrahive_core.subagent_isolation import TOOL_CATEGORIES, ToolCategory
    missing = [
        tid for tid in _CORE_TOOL_IDS_FROM_REGISTRY
        if tid not in TOOL_CATEGORIES
    ]
    assert not missing, (
        f"Core-Tools ohne explizite Subagent-Isolation-Category: {missing}. "
        "Bitte in subagent_isolation.TOOL_CATEGORIES eintragen."
    )
    # Sanity: kein Core-Tool sollte explizit auf UNKNOWN gemappt sein
    explicit_unknown = [
        tid for tid in _CORE_TOOL_IDS_FROM_REGISTRY
        if TOOL_CATEGORIES.get(tid) == ToolCategory.UNKNOWN
    ]
    assert not explicit_unknown, f"Tools auf UNKNOWN explizit gemappt: {explicit_unknown}"
