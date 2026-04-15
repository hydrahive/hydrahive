"""Tests für subagent_write_scope (#653)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hydrahive_core.subagent_write_scope import (
    IMPLICIT_DENY_PATTERNS,
    MAX_PATTERN_LEN,
    WriteScope,
    WriteScopeError,
    WriteScopeReport,
    WriteScopeViolation,
    _glob_to_regex,
    changed_files,
    evaluate_worktree_scope,
    path_allowed,
    validate_write_scope,
    write_scope_to_dict,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd),
        capture_output=True, text=True, check=False,
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.email", "t@e.com"], repo)
    _git(["config", "user.name", "T"], repo)
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    (repo / "core").mkdir(exist_ok=True)
    (repo / "core" / "a.py").write_text("a\n", encoding="utf-8")
    _git(["add", "."], repo)
    _git(["commit", "-q", "-m", "init"], repo)


@pytest.fixture
def repo(tmp_path):
    p = tmp_path / "repo"
    _init_repo(p)
    return p


# ── Validation ───────────────────────────────────────────────────────────────

def test_validate_none_is_empty_scope():
    s = validate_write_scope(None)
    assert s.allow == () and s.deny == () and s.description is None
    assert s.is_empty_allow


def test_validate_empty_dict():
    s = validate_write_scope({})
    assert s.allow == () and s.deny == ()


def test_validate_non_dict_rejected():
    with pytest.raises(WriteScopeError, match="object or null"):
        validate_write_scope("nope")  # type: ignore[arg-type]


def test_validate_unknown_field_rejected():
    with pytest.raises(WriteScopeError, match="unknown fields"):
        validate_write_scope({"allow": [], "deny": [], "extra": 1})


def test_validate_allow_must_be_array():
    with pytest.raises(WriteScopeError, match="allow must be array"):
        validate_write_scope({"allow": "core/**"})


def test_validate_description_must_be_string():
    with pytest.raises(WriteScopeError, match="description must be string"):
        validate_write_scope({"description": 42})


@pytest.mark.parametrize("bad", [
    "",                    # leer
    "/abs/path",           # absolut
    "a/../b",              # ".." Segment
    "with\\back",          # Backslash
    "x" * (MAX_PATTERN_LEN + 1),  # zu lang
    "null\x00byte",        # NUL
])
def test_validate_rejects_bad_patterns(bad):
    with pytest.raises(WriteScopeError):
        validate_write_scope({"allow": [bad]})


def test_validate_happy():
    s = validate_write_scope({
        "allow": ["core/src/**", "core/tests/**"],
        "deny":  ["**/*.env", "**/*secret*"],
        "description": "core-only",
    })
    assert s.allow == ("core/src/**", "core/tests/**")
    assert s.deny == ("**/*.env", "**/*secret*")
    assert s.description == "core-only"


def test_to_dict_roundtrip():
    s = validate_write_scope({"allow": ["a/**"], "deny": ["b"], "description": "d"})
    d = write_scope_to_dict(s)
    assert d == {"allow": ["a/**"], "deny": ["b"], "description": "d"}
    s2 = validate_write_scope(d)
    assert s2 == s


# ── Glob-Matcher (laut Review-Vorgabe) ───────────────────────────────────────

@pytest.mark.parametrize("pat,path,expected", [
    # core/**
    ("core/**",       "core/x",       True),
    ("core/**",       "core/a/b.py",  True),
    ("core/**",       "core/",        True),
    ("core/**",       "core2/x",      False),
    # *.py
    ("*.py",          "foo.py",       True),
    ("*.py",          "a/foo.py",     False),
    # **/*.py — gitignore-Style: matcht auch ohne Prefix
    ("**/*.py",       "foo.py",       True),
    ("**/*.py",       "a/foo.py",     True),
    ("**/*.py",       "a/b/foo.py",   True),
    ("**/*.py",       "foo.txt",      False),
    # **/*secret*
    ("**/*secret*",   "secret.env",            True),
    ("**/*secret*",   "config/my_secret.yaml", True),
    ("**/*secret*",   "config/normal.yaml",    False),
    # ? — single char
    ("a?c",           "abc",   True),
    ("a?c",           "abbc",  False),
    # Literal
    ("README.md",     "README.md",  True),
    ("README.md",     "readme.md",  False),  # case-sensitive
])
def test_glob_matcher(pat, path, expected):
    assert bool(_glob_to_regex(pat).match(path)) is expected


# ── path_allowed ─────────────────────────────────────────────────────────────

def test_implicit_deny_git():
    s = WriteScope(allow=("**",), deny=(), description=None)
    ok, reason = path_allowed(s, ".git/HEAD")
    assert ok is False
    assert "implicit_deny" in reason


def test_implicit_deny_git_dir_itself():
    s = WriteScope()
    ok, _ = path_allowed(s, ".git")
    assert ok is False


def test_explicit_deny_beats_allow():
    s = WriteScope(allow=("**",), deny=("**/*.env",), description=None)
    ok, reason = path_allowed(s, "config/secret.env")
    assert ok is False
    assert "deny" in reason


def test_empty_allow_permits_everything_outside_deny():
    # V1-Default: leere allow-Liste = alles erlaubt außer implicit/user deny.
    s = WriteScope(allow=(), deny=("**/*.env",), description=None)
    ok, reason = path_allowed(s, "core/x.py")
    assert ok is True
    assert "no allow-list" in reason


def test_nonempty_allow_blocks_out_of_scope():
    s = WriteScope(allow=("core/**",), deny=(), description=None)
    ok, reason = path_allowed(s, "other/x.py")
    assert ok is False
    assert reason == "out_of_scope"


def test_allow_match():
    s = WriteScope(allow=("core/**",), deny=(), description=None)
    ok, reason = path_allowed(s, "core/a.py")
    assert ok is True
    assert "allow" in reason


def test_path_with_dotdot_rejected():
    with pytest.raises(WriteScopeError, match=r"\.\."):
        path_allowed(WriteScope(), "a/../b")


def test_path_absolute_rejected():
    with pytest.raises(WriteScopeError, match="absolute"):
        path_allowed(WriteScope(), "/etc/passwd")


def test_path_backslash_rejected():
    with pytest.raises(WriteScopeError, match="backslash"):
        path_allowed(WriteScope(), "a\\b")


def test_path_dot_slash_stripped():
    s = WriteScope(allow=("core/**",), deny=(), description=None)
    ok, _ = path_allowed(s, "./core/a.py")
    assert ok is True


# ── changed_files (echter Git-Repo) ──────────────────────────────────────────

def test_changed_files_clean(repo):
    assert changed_files(repo) == []


def test_changed_files_modified(repo):
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    assert "README.md" in changed_files(repo)


def test_changed_files_untracked(repo):
    (repo / "new.txt").write_text("hello\n", encoding="utf-8")
    assert "new.txt" in changed_files(repo)


def test_changed_files_deleted(repo):
    (repo / "README.md").unlink()
    assert "README.md" in changed_files(repo)


def test_changed_files_renamed(repo):
    _git(["mv", "core/a.py", "core/b.py"], repo)
    files = changed_files(repo)
    # Git rename: alter + neuer Pfad beide enthalten
    assert "core/b.py" in files
    assert "core/a.py" in files


def test_changed_files_non_existent_path(tmp_path):
    with pytest.raises(WriteScopeError, match="does not exist"):
        changed_files(tmp_path / "nope")


# ── evaluate_worktree_scope (echter Worktree) ────────────────────────────────

@pytest.fixture
def worktrees(tmp_path, monkeypatch):
    d = tmp_path / "wt"
    monkeypatch.setenv("HYDRAHIVE_WORKTREES_DIR", str(d))
    return d


def _make_worktree(repo, worktrees):
    from hydrahive_core.subagent_worktrees import create_worktree
    return create_worktree(
        base_repo=repo,
        parent_project_id="p",
        parent_agent_id="boss",
        sub_agent_id="sub",
        task_id="t",
    )


def test_evaluate_clean_worktree(repo, worktrees):
    meta = _make_worktree(repo, worktrees)
    scope = WriteScope()
    r = evaluate_worktree_scope(meta.worktree_path, scope)
    assert r.ok is True
    assert r.violations_count == 0
    assert r.allowed_files == ()


def test_evaluate_changes_in_allow(repo, worktrees):
    meta = _make_worktree(repo, worktrees)
    (Path(meta.worktree_path) / "core" / "a.py").write_text("change\n", encoding="utf-8")
    scope = validate_write_scope({"allow": ["core/**"], "deny": []})
    r = evaluate_worktree_scope(meta.worktree_path, scope)
    assert r.ok is True
    assert "core/a.py" in r.allowed_files
    assert r.denied_files == ()
    assert r.out_of_scope_files == ()


def test_evaluate_change_in_deny(repo, worktrees):
    meta = _make_worktree(repo, worktrees)
    # neue .env-Datei — matcht gängigen deny
    (Path(meta.worktree_path) / "secrets.env").write_text("API=x\n", encoding="utf-8")
    scope = validate_write_scope({"allow": ["**"], "deny": ["**/*.env"]})
    r = evaluate_worktree_scope(meta.worktree_path, scope)
    assert r.ok is False
    assert "secrets.env" in r.denied_files
    assert r.violations_count == 1
    v = r.violations[0]
    assert v.path == "secrets.env"
    assert "deny" in v.reason


def test_evaluate_change_out_of_scope(repo, worktrees):
    meta = _make_worktree(repo, worktrees)
    (Path(meta.worktree_path) / "README.md").write_text("changed\n", encoding="utf-8")
    scope = validate_write_scope({"allow": ["core/**"], "deny": []})
    r = evaluate_worktree_scope(meta.worktree_path, scope)
    assert r.ok is False
    assert "README.md" in r.out_of_scope_files
    assert any(v.path == "README.md" and v.reason == "out_of_scope" for v in r.violations)


def test_evaluate_wrong_type_scope(repo, worktrees):
    meta = _make_worktree(repo, worktrees)
    with pytest.raises(WriteScopeError, match="WriteScope"):
        evaluate_worktree_scope(meta.worktree_path, {"allow": []})  # type: ignore[arg-type]


def test_implicit_deny_applied_via_path_allowed():
    """path_allowed blockt .git/* selbst bei aggressivem allow."""
    s = WriteScope(allow=("**",), deny=(), description=None)
    ok, reason = path_allowed(s, ".git/config")
    assert ok is False
    assert "implicit_deny" in reason


def test_implicit_deny_patterns_constant():
    assert ".git" in IMPLICIT_DENY_PATTERNS
    assert ".git/**" in IMPLICIT_DENY_PATTERNS
