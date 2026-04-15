"""Tests für subagent_isolation (#652)."""
from __future__ import annotations

import pytest

from hydrahive_core.subagent_isolation import (
    ALLOWED_ISOLATION_MODES,
    DEFAULT_ISOLATION_MODE,
    IsolationDecision,
    IsolationError,
    IsolationMode,
    ToolCategory,
    allow_tool,
    tool_category,
    validate_isolation_mode,
)


# ── Konstanten / Default ─────────────────────────────────────────────────────

def test_allowed_modes_exact():
    assert ALLOWED_ISOLATION_MODES == frozenset(
        {"read_only", "patch_only", "full_worktree"}
    )


def test_default_is_full_worktree():
    assert DEFAULT_ISOLATION_MODE == IsolationMode.FULL_WORKTREE
    assert DEFAULT_ISOLATION_MODE.value == "full_worktree"


# ── validate_isolation_mode ──────────────────────────────────────────────────

@pytest.mark.parametrize("raw", ["read_only", "patch_only", "full_worktree"])
def test_validate_accepts_canonical_strings(raw):
    assert validate_isolation_mode(raw).value == raw


def test_validate_accepts_enum():
    assert validate_isolation_mode(IsolationMode.READ_ONLY) is IsolationMode.READ_ONLY


@pytest.mark.parametrize("bad", ["Read_Only", "PATCH_ONLY", "full-worktree", "worktree", "", "yolo"])
def test_validate_rejects_unknown_and_case(bad):
    with pytest.raises(IsolationError, match="invalid isolation_mode"):
        validate_isolation_mode(bad)


def test_validate_rejects_wrong_type():
    with pytest.raises(IsolationError, match="must be str or IsolationMode"):
        validate_isolation_mode(42)  # type: ignore[arg-type]


# ── tool_category ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "name,expected",
    [
        ("file_read",        ToolCategory.READ),
        ("file_search",      ToolCategory.READ),
        ("read_memory",      ToolCategory.READ),
        ("web_search",       ToolCategory.READ),
        ("git_status",       ToolCategory.READ),
        ("git_log",          ToolCategory.READ),
        ("git_diff",         ToolCategory.READ),
        ("file_write",       ToolCategory.WRITE),
        ("file_patch",       ToolCategory.WRITE),
        ("write_memory",     ToolCategory.WRITE),
        ("shell_exec",       ToolCategory.SHELL),
        ("git_clone",        ToolCategory.GIT_MUTATE),
        ("git_commit_all",   ToolCategory.GIT_MUTATE),
        ("git_branch",       ToolCategory.GIT_MUTATE),
        ("git_pull",         ToolCategory.GIT_MUTATE),
        ("git_reset",        ToolCategory.GIT_MUTATE),
        ("git_push",         ToolCategory.GIT_PUSH),
        ("ask_agent",        ToolCategory.NETWORK),
        ("tool_search",      ToolCategory.META),
        ("get_final_message", ToolCategory.META),
    ],
)
def test_tool_category_known(name, expected):
    assert tool_category(name) == expected


def test_tool_category_unknown():
    assert tool_category("nonexistent_tool_xyz") == ToolCategory.UNKNOWN
    assert tool_category("") == ToolCategory.UNKNOWN


# ── Policy-Matrix: 3 Modes × 8 Kategorien = 24 Einträge ─────────────────────

# True = allowed, False = blocked
MATRIX = {
    (IsolationMode.READ_ONLY,      ToolCategory.READ):       True,
    (IsolationMode.READ_ONLY,      ToolCategory.WRITE):      False,
    (IsolationMode.READ_ONLY,      ToolCategory.SHELL):      False,
    (IsolationMode.READ_ONLY,      ToolCategory.GIT_MUTATE): False,
    (IsolationMode.READ_ONLY,      ToolCategory.GIT_PUSH):   False,
    (IsolationMode.READ_ONLY,      ToolCategory.NETWORK):    True,
    (IsolationMode.READ_ONLY,      ToolCategory.META):       True,
    (IsolationMode.READ_ONLY,      ToolCategory.UNKNOWN):    False,

    (IsolationMode.PATCH_ONLY,     ToolCategory.READ):       True,
    (IsolationMode.PATCH_ONLY,     ToolCategory.WRITE):      False,
    (IsolationMode.PATCH_ONLY,     ToolCategory.SHELL):      False,
    (IsolationMode.PATCH_ONLY,     ToolCategory.GIT_MUTATE): False,
    (IsolationMode.PATCH_ONLY,     ToolCategory.GIT_PUSH):   False,
    (IsolationMode.PATCH_ONLY,     ToolCategory.NETWORK):    True,
    (IsolationMode.PATCH_ONLY,     ToolCategory.META):       True,
    (IsolationMode.PATCH_ONLY,     ToolCategory.UNKNOWN):    False,

    (IsolationMode.FULL_WORKTREE,  ToolCategory.READ):       True,
    (IsolationMode.FULL_WORKTREE,  ToolCategory.WRITE):      True,
    (IsolationMode.FULL_WORKTREE,  ToolCategory.SHELL):      True,
    (IsolationMode.FULL_WORKTREE,  ToolCategory.GIT_MUTATE): True,
    (IsolationMode.FULL_WORKTREE,  ToolCategory.GIT_PUSH):   False,
    (IsolationMode.FULL_WORKTREE,  ToolCategory.NETWORK):    True,
    (IsolationMode.FULL_WORKTREE,  ToolCategory.META):       True,
    (IsolationMode.FULL_WORKTREE,  ToolCategory.UNKNOWN):    True,
}


_REPRESENTATIVE_TOOL = {
    ToolCategory.READ:       "file_read",
    ToolCategory.WRITE:      "file_write",
    ToolCategory.SHELL:      "shell_exec",
    ToolCategory.GIT_MUTATE: "git_commit_all",
    ToolCategory.GIT_PUSH:   "git_push",
    ToolCategory.NETWORK:    "ask_agent",
    ToolCategory.META:       "tool_search",
    ToolCategory.UNKNOWN:    "something_not_in_table",
}


@pytest.mark.parametrize("mode,cat,expected", [
    (m, c, MATRIX[(m, c)])
    for m in IsolationMode
    for c in ToolCategory
])
def test_policy_matrix(mode, cat, expected):
    tool = _REPRESENTATIVE_TOOL[cat]
    d = allow_tool(mode, tool)
    assert isinstance(d, IsolationDecision)
    assert d.allowed is expected, (
        f"mode={mode.value} category={cat.value} tool={tool} expected {expected}, got {d}"
    )
    assert d.reason  # non-empty string


# ── Explizit laut Review-Vorgabe ─────────────────────────────────────────────

def test_full_worktree_allows_unknown_tool():
    # UNKNOWN ist in full_worktree erlaubt (Isolations-Modell ist ohnehin
    # full-write, ein unbekanntes Tool ist dort nicht gefährlicher als ein
    # erlaubtes Write-Tool).
    d = allow_tool(IsolationMode.FULL_WORKTREE, "nonexistent_tool")
    assert d.allowed is True
    assert "unknown" in d.reason.lower()


def test_full_worktree_blocks_git_push():
    # Explizit: git_push bleibt auch im full_worktree blockiert —
    # Auto-Merge-/Remote-Sperre laut V1-Vorgabe.
    d = allow_tool(IsolationMode.FULL_WORKTREE, "git_push")
    assert d.allowed is False
    assert "git_push" in d.reason


def test_read_only_allows_ask_agent():
    # ask_agent ist in V1 als NETWORK / lokal-nicht-schreibend klassifiziert.
    # Transitive Effekte des aufgerufenen Ziel-Agenten werden NICHT von
    # dieser Policy erfasst — das passiert erst bei Runtime-Integration
    # (#662/#653). allow=True bedeutet hier: der Aufruf selbst ist aus
    # Sicht des lokalen Workspace kein Write.
    d = allow_tool(IsolationMode.READ_ONLY, "ask_agent")
    assert d.allowed is True


# ── allow_tool akzeptiert auch String-Mode ───────────────────────────────────

def test_allow_tool_accepts_string_mode():
    d = allow_tool("read_only", "file_read")
    assert d.allowed is True


def test_allow_tool_rejects_invalid_mode():
    with pytest.raises(IsolationError):
        allow_tool("not_a_mode", "file_read")
