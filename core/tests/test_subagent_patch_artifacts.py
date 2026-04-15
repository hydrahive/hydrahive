"""Tests für subagent_patch_artifacts (#665)."""
from __future__ import annotations

from pathlib import Path

import pytest

from hydrahive_core.subagent_patch_artifacts import (
    PatchArtifactError,
    PatchArtifactReport,
    build_patch_artifact_report,
    extract_fenced_diff,
    parse_unified_diff_paths,
    persist_patch_artifact,
    validate_patch_paths,
)
from hydrahive_core.subagent_write_scope import WriteScope, validate_write_scope


# ── extract_fenced_diff ──────────────────────────────────────────────────────

def test_extract_fenced_diff_block():
    text = """Hier ist der Vorschlag:

```diff
--- a/core/foo.py
+++ b/core/foo.py
@@ -1 +1 @@
-old
+new
```

Fertig.
"""
    content, count = extract_fenced_diff(text)
    assert count == 1
    assert content is not None
    assert "core/foo.py" in content
    assert "+new" in content


def test_extract_fenced_patch_alias():
    text = "```patch\n--- a/x\n+++ b/x\n@@ @@\n```\n"
    content, count = extract_fenced_diff(text)
    assert count == 1
    assert content is not None


def test_extract_no_fenced_block():
    content, count = extract_fenced_diff("just prose without any diff content")
    assert content is None
    assert count == 0


def test_extract_multiple_diff_blocks():
    text = "```diff\n--- a/a\n+++ b/a\n@@@@\n```\n\n```diff\n--- a/b\n+++ b/b\n@@@@\n```\n"
    content, count = extract_fenced_diff(text)
    assert count == 2
    assert "a/a" in content  # erster wird extrahiert


def test_extract_python_block_ignored():
    text = "```python\nprint('not a diff')\n```\n"
    content, count = extract_fenced_diff(text)
    assert content is None
    assert count == 0


def test_extract_empty_text():
    content, count = extract_fenced_diff("")
    assert content is None
    assert count == 0


# ── parse_unified_diff_paths ────────────────────────────────────────────────

def test_parse_git_header():
    diff = "diff --git a/core/foo.py b/core/foo.py\n--- a/core/foo.py\n+++ b/core/foo.py\n"
    assert parse_unified_diff_paths(diff) == ["core/foo.py"]


def test_parse_minus_plus_headers():
    diff = "--- a/core/foo.py\n+++ b/core/foo.py\n@@@@\n"
    assert parse_unified_diff_paths(diff) == ["core/foo.py"]


def test_parse_new_file():
    diff = "--- /dev/null\n+++ b/new/file.py\n"
    assert parse_unified_diff_paths(diff) == ["new/file.py"]


def test_parse_deleted_file():
    diff = "--- a/gone.py\n+++ /dev/null\n"
    assert parse_unified_diff_paths(diff) == ["gone.py"]


def test_parse_rename():
    diff = (
        "diff --git a/old.py b/new.py\n"
        "rename from old.py\n"
        "rename to new.py\n"
    )
    paths = parse_unified_diff_paths(diff)
    assert "old.py" in paths
    assert "new.py" in paths


def test_parse_multi_file():
    diff = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
        "diff --git a/dir/b.py b/dir/b.py\n--- a/dir/b.py\n+++ b/dir/b.py\n"
    )
    paths = parse_unified_diff_paths(diff)
    assert paths == ["a.py", "dir/b.py"]


def test_parse_strips_tab_timestamp():
    diff = "--- a/foo.py\t2026-04-15 12:00:00\n+++ b/foo.py\t2026-04-15 12:00:01\n"
    assert parse_unified_diff_paths(diff) == ["foo.py"]


# ── validate_patch_paths Safety ──────────────────────────────────────────────

def test_safety_absolute_rejected():
    safe, viols = validate_patch_paths(["/etc/passwd"], None)
    assert safe == []
    assert any("absolute_path" in v for v in viols)


def test_safety_dotdot_rejected():
    safe, viols = validate_patch_paths(["../etc/passwd"], None)
    assert safe == []
    assert any("dotdot_segment" in v for v in viols)


def test_safety_backslash_rejected():
    safe, viols = validate_patch_paths(["a\\b"], None)
    assert safe == []
    assert any("backslash" in v for v in viols)


def test_safety_empty_rejected():
    safe, viols = validate_patch_paths([""], None)
    assert safe == []


def test_safety_relative_accepted_without_scope():
    safe, viols = validate_patch_paths(["core/foo.py"], None)
    assert safe == ["core/foo.py"]
    assert viols == []


# ── validate_patch_paths WriteScope ──────────────────────────────────────────

def test_scope_allow_matches():
    scope = validate_write_scope({"allow": ["core/**"]})
    safe, viols = validate_patch_paths(["core/foo.py", "docs/a.md"], scope)
    assert safe == ["core/foo.py"]
    assert any("out_of_scope" in v for v in viols)


def test_scope_deny_blocks():
    scope = validate_write_scope({"allow": ["**"], "deny": ["**/*.env"]})
    safe, viols = validate_patch_paths(["src/a.py", "config/prod.env"], scope)
    assert safe == ["src/a.py"]
    assert any(".env" in v and "deny" in v for v in viols)


def test_implicit_deny_git_dir():
    # implicit-deny (.git/**) greift innerhalb WriteScope-Check. Mit scope=None
    # wird nur Safety geprüft — da ist .git/HEAD ein valider relativer Pfad.
    # Für implicit-deny brauchen wir eine WriteScope-Instanz (leer genügt).
    scope = WriteScope()
    safe, viols = validate_patch_paths([".git/HEAD"], scope)
    assert safe == []
    assert any("implicit_deny" in v for v in viols)


# ── persist_patch_artifact ──────────────────────────────────────────────────

def test_persist_writes_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HYDRAHIVE_WORKTREES_DIR", str(tmp_path))
    wid = "wt-20260101T000000Z-sub-aaaaaaaa"
    diff_text = "--- a/x\n+++ b/x\n@@ @@\n-old\n+new\n"
    path = persist_patch_artifact(wid, diff_text)
    assert path.exists()
    assert path.read_text(encoding="utf-8") == diff_text
    assert path.parent == tmp_path / "artifacts"


def test_persist_invalid_worktree_id(tmp_path, monkeypatch):
    monkeypatch.setenv("HYDRAHIVE_WORKTREES_DIR", str(tmp_path))
    with pytest.raises(PatchArtifactError, match="invalid worktree_id"):
        persist_patch_artifact("../etc/passwd", "diff")


# ── build_patch_artifact_report ─────────────────────────────────────────────

WID = "wt-20260101T000000Z-sub-bbbbbbbb"


def test_report_no_diff(tmp_path, monkeypatch):
    monkeypatch.setenv("HYDRAHIVE_WORKTREES_DIR", str(tmp_path))
    r = build_patch_artifact_report("just text", None, WID)
    assert r.present is False
    assert r.valid is False
    assert r.error == "no_diff_block_found"
    assert r.artifact_path is None
    assert r.bytes is None


def test_report_valid_with_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("HYDRAHIVE_WORKTREES_DIR", str(tmp_path))
    scope = validate_write_scope({"allow": ["core/**"]})
    text = (
        "```diff\n"
        "diff --git a/core/foo.py b/core/foo.py\n"
        "--- a/core/foo.py\n"
        "+++ b/core/foo.py\n"
        "@@ @@\n"
        "-old\n"
        "+new\n"
        "```\n"
    )
    r = build_patch_artifact_report(text, scope, WID)
    assert r.present is True
    assert r.valid is True
    assert r.paths == ("core/foo.py",)
    assert r.violations == ()
    assert r.error is None
    assert r.artifact_path is not None
    assert Path(r.artifact_path).exists()
    assert r.bytes is not None and r.bytes > 0


def test_report_invalid_persisted_for_audit(tmp_path, monkeypatch):
    """Invalid Artifact wird trotzdem persistiert (Audit-Trail)."""
    monkeypatch.setenv("HYDRAHIVE_WORKTREES_DIR", str(tmp_path))
    text = (
        "```diff\n"
        "--- a/../etc/passwd\n"
        "+++ b/../etc/passwd\n"
        "@@ @@\n"
        "```\n"
    )
    r = build_patch_artifact_report(text, None, WID)
    assert r.present is True
    assert r.valid is False
    assert any("unsafe_path" in v and "dotdot" in v for v in r.violations)
    # Persistiert für Audit:
    assert r.artifact_path is not None
    assert Path(r.artifact_path).exists()


def test_report_scope_violation(tmp_path, monkeypatch):
    monkeypatch.setenv("HYDRAHIVE_WORKTREES_DIR", str(tmp_path))
    scope = validate_write_scope({"allow": ["core/**"], "deny": []})
    text = (
        "```diff\n"
        "diff --git a/docs/a.md b/docs/a.md\n"
        "--- a/docs/a.md\n"
        "+++ b/docs/a.md\n"
        "@@ @@\n"
        "```\n"
    )
    r = build_patch_artifact_report(text, scope, WID)
    assert r.present is True
    assert r.valid is False
    assert "docs/a.md" in r.paths
    assert any("out_of_scope" in v for v in r.violations)


def test_report_multiple_blocks(tmp_path, monkeypatch):
    monkeypatch.setenv("HYDRAHIVE_WORKTREES_DIR", str(tmp_path))
    text = (
        "```diff\n--- a/a\n+++ b/a\n@@ @@\n```\n"
        "some text\n"
        "```diff\n--- a/b\n+++ b/b\n@@ @@\n```\n"
    )
    r = build_patch_artifact_report(text, None, WID)
    assert r.present is True
    assert r.valid is False
    assert r.error == "multiple_diff_blocks"
    # Erster Block persistiert
    assert r.artifact_path is not None
    content = Path(r.artifact_path).read_text()
    assert "a/a" in content
    assert "a/b" not in content


def test_report_no_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("HYDRAHIVE_WORKTREES_DIR", str(tmp_path))
    text = "```diff\n--- a/x\n+++ b/x\n@@ @@\n```\n"
    r = build_patch_artifact_report(text, None, WID, persist=False)
    assert r.present is True
    assert r.artifact_path is None
    assert r.bytes is None
