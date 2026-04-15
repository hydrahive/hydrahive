"""Tests für subagent_write_scope (#653)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hydrahive_core.subagent_write_scope import (
    IMPLICIT_DENY_PATTERNS,
    MAX_PATTERN_LEN,
    ScopeOverlap,
    WriteScope,
    WriteScopeError,
    WriteScopeReport,
    WriteScopeViolation,
    _classify_pair,
    _classify_pattern,
    _glob_to_regex,
    changed_files,
    compare_many_scopes,
    compare_scopes,
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


# ── Pattern-Classifier (#666) ────────────────────────────────────────────────

@pytest.mark.parametrize("pat,kind,payload", [
    ("core/a.py",   "literal",    ("core/a.py",)),
    ("README.md",   "literal",    ("README.md",)),
    ("**",          "catch_all",  ()),
    ("**/*",        "catch_all",  ()),
    ("core/**",     "prefix",     ("core",)),
    ("core/src/**", "prefix",     ("core/src",)),
    ("**/*.py",     "ext_any",    ("py",)),
    ("**/*.yaml",   "ext_any",    ("yaml",)),
    ("core/*.py",   "ext_scoped", ("core", "py")),
    ("*.py",        "ext_scoped", ("", "py")),
    # Unklare Muster → complex
    ("**/config.*",    "complex", ()),
    ("core/**/*.py",   "complex", ()),
    ("*/foo.py",       "complex", ()),
    ("**/*secret*",    "complex", ()),
])
def test_classify_pattern(pat, kind, payload):
    k, v = _classify_pattern(pat)
    assert k == kind
    assert v == payload


# ── _classify_pair Matrix ────────────────────────────────────────────────────

@pytest.mark.parametrize("pa,pb,expected", [
    # identisch
    ("core/**",       "core/**",       "overlap"),
    # prefix-superset
    ("core/**",       "core/src/**",   "overlap"),
    ("core/src/**",   "core/**",       "overlap"),
    # disjoint prefix
    ("core/**",       "console/**",    "safe"),
    ("core/**",       "core2/**",      "safe"),
    # literal × literal
    ("core/a.py",     "core/b.py",     "safe"),
    ("core/a.py",     "core/a.py",     "overlap"),
    # literal × prefix
    ("core/a.py",     "core/**",       "overlap"),
    ("core/a.py",     "console/**",    "safe"),
    # literal × ext_any
    ("**/*.py",       "core/foo.py",   "overlap"),
    ("**/*.py",       "core/foo.txt",  "safe"),
    # literal × ext_scoped
    ("core/*.py",     "core/foo.py",   "overlap"),
    ("core/*.py",     "core/sub/foo.py", "safe"),
    ("core/*.py",     "core/foo.txt",  "safe"),
    # prefix × ext_any
    ("core/**",       "**/*.py",       "overlap"),
    # prefix × ext_scoped
    ("core/*.py",     "core/**",       "overlap"),
    ("core/sub/*.py", "core/**",       "overlap"),
    ("core/*.py",     "console/**",    "safe"),
    # ext_any × ext_any
    ("**/*.py",       "**/*.py",       "overlap"),
    ("**/*.py",       "**/*.txt",      "safe"),
    # ext_any × ext_scoped
    ("**/*.py",       "core/*.py",     "overlap"),
    ("**/*.txt",      "core/*.py",     "safe"),
    # ext_scoped × ext_scoped
    ("core/*.py",     "console/*.py",  "safe"),
    ("core/*.py",     "core/*.txt",    "safe"),
    ("core/*.py",     "core/*.py",     "overlap"),
    # catch-all "**" schluckt alles
    ("**",            "core/a.py",     "overlap"),
    ("**",            "console/**",    "overlap"),
    # complex → uncertain
    ("*/foo.py",      "core/*.py",     "uncertain"),
    ("**/config.*",   "core/**",       "uncertain"),
    ("**/*secret*",   "core/**",       "uncertain"),
])
def test_classify_pair_matrix(pa, pb, expected):
    assert _classify_pair(pa, pb) == expected
    # Symmetrie
    assert _classify_pair(pb, pa) == expected


# ── compare_scopes: Allow-Geometrie ──────────────────────────────────────────

def _ws(allow=(), deny=()):
    return WriteScope(allow=tuple(allow), deny=tuple(deny), description=None)


def test_compare_identical_allow_overlap():
    r = compare_scopes(_ws(["core/**"]), _ws(["core/**"]))
    assert r.status == "overlap"
    assert ("core/**", "core/**") in r.overlapping_patterns


def test_compare_prefix_superset_overlap():
    r = compare_scopes(_ws(["core/**"]), _ws(["core/src/**"]))
    assert r.status == "overlap"


def test_compare_disjoint_prefix_safe():
    r = compare_scopes(_ws(["core/**"]), _ws(["console/**"]))
    assert r.status == "safe"
    assert r.overlapping_patterns == ()
    assert r.uncertain_patterns == ()


def test_compare_different_literal_files_safe():
    r = compare_scopes(_ws(["core/a.py"]), _ws(["core/b.py"]))
    assert r.status == "safe"


def test_compare_both_empty_allow_overlap():
    r = compare_scopes(_ws(), _ws())
    assert r.status == "overlap"


def test_compare_empty_vs_nonempty_overlap_or_uncertain():
    r = compare_scopes(_ws(), _ws(["core/**"]))
    assert r.status in ("overlap", "uncertain")
    assert r.status != "safe"


def test_compare_ext_any_vs_literal_overlap():
    r = compare_scopes(_ws(["**/*.py"]), _ws(["core/foo.py"]))
    assert r.status == "overlap"


def test_compare_ext_any_vs_literal_safe():
    r = compare_scopes(_ws(["**/*.py"]), _ws(["core/foo.txt"]))
    assert r.status == "safe"


def test_compare_complex_wildcard_uncertain():
    r = compare_scopes(_ws(["*/foo.py"]), _ws(["core/*.py"]))
    assert r.status == "uncertain"


def test_compare_complex_wildcard_never_safe():
    r = compare_scopes(_ws(["**/config.*"]), _ws(["core/**"]))
    assert r.status != "safe"


# ── compare_scopes: Deny-Regeln (V1) ────────────────────────────────────────

def test_deny_downgrades_overlap_to_uncertain():
    """
    [core/**] deny [core/secrets/**] vs [core/secrets/**] → uncertain,
    weil deny möglicherweise den overlap ausschließt, aber nicht mit
    Sicherheit.
    """
    a = _ws(["core/**"], ["core/secrets/**"])
    b = _ws(["core/secrets/**"])
    r = compare_scopes(a, b)
    assert r.status == "uncertain"


def test_deny_does_not_affect_disjoint_allow_geometry():
    """
    [core/**] deny [core/secrets/**] vs [console/**] → safe bleibt safe,
    weil Allow-Geometrie disjunkt ist.
    """
    a = _ws(["core/**"], ["core/secrets/**"])
    b = _ws(["console/**"])
    r = compare_scopes(a, b)
    assert r.status == "safe"


def test_deny_never_makes_overlap_safe():
    """
    [core/**] deny [core/**] vs [core/**] → uncertain, nicht safe,
    obwohl deny theoretisch alles ausschließt.
    """
    a = _ws(["core/**"], ["core/**"])
    b = _ws(["core/**"])
    r = compare_scopes(a, b)
    assert r.status == "uncertain"


def test_both_empty_allow_with_deny_is_uncertain():
    a = _ws([], ["**/*.env"])
    b = _ws([], [])
    r = compare_scopes(a, b)
    assert r.status == "uncertain"


# ── compare_many_scopes ──────────────────────────────────────────────────────

def test_compare_many_scopes_pairs():
    scopes = {
        "alice":   _ws(["core/**"]),
        "bob":     _ws(["console/**"]),
        "charlie": _ws(["core/src/**"]),
    }
    report = compare_many_scopes(scopes)
    assert set(report.keys()) == {
        ("alice", "bob"),
        ("alice", "charlie"),
        ("bob", "charlie"),
    }
    assert report[("alice", "bob")].status == "safe"
    assert report[("alice", "charlie")].status == "overlap"
    assert report[("bob", "charlie")].status == "safe"


def test_compare_many_scopes_rejects_non_writescope():
    with pytest.raises(WriteScopeError, match="WriteScope"):
        compare_many_scopes({"a": _ws(["core/**"]), "b": {"allow": []}})  # type: ignore[dict-item]


def test_compare_many_scopes_rejects_non_dict():
    with pytest.raises(WriteScopeError, match="dict"):
        compare_many_scopes([("a", _ws())])  # type: ignore[arg-type]


def test_compare_scopes_rejects_non_writescope():
    with pytest.raises(WriteScopeError, match="WriteScope"):
        compare_scopes({"allow": []}, _ws())  # type: ignore[arg-type]


def test_scope_overlap_is_frozen():
    r = compare_scopes(_ws(["core/**"]), _ws(["console/**"]))
    with pytest.raises(Exception):
        r.status = "overlap"  # type: ignore[misc]
