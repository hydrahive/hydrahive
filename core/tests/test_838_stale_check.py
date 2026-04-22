"""
test_838_stale_check.py — Gate 2: Pre-Patch Stale-Check (#838).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True, timeout=30)


def _make_repo_pair(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Erstellt origin + clone. Returns (origin_path, clone_path, file_in_clone)."""
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    clone = tmp_path / "clone"

    # bare origin
    _git(["init", "--bare", "--initial-branch=main", str(origin)], tmp_path)
    # work-Dir mit initial commit
    work.mkdir()
    _git(["init", "--initial-branch=main"], work)
    _git(["config", "user.email", "t@t.t"], work)
    _git(["config", "user.name", "T"], work)
    f = work / "foo.py"
    f.write_text("x = 1\n")
    _git(["add", "."], work)
    _git(["commit", "-m", "init"], work)
    _git(["remote", "add", "origin", str(origin)], work)
    _git(["push", "origin", "main"], work)
    # clone
    _git(["clone", str(origin), str(clone)], tmp_path)
    _git(["config", "user.email", "t@t.t"], clone)
    _git(["config", "user.name", "T"], clone)
    return (origin, clone, clone / "foo.py")


# ─── find_repo_root ────────────────────────────────────────────────────

def test_find_repo_root_finds_git(tmp_path):
    from hydrahive_core.patch_stale_check import find_repo_root
    repo = tmp_path / "myrepo"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "src"
    sub.mkdir()
    f = sub / "x.py"
    f.write_text("x")
    assert find_repo_root(f) == repo.resolve()


def test_find_repo_root_returns_none(tmp_path):
    from hydrahive_core.patch_stale_check import find_repo_root
    f = tmp_path / "lonely.py"
    f.write_text("x")
    assert find_repo_root(f) is None


# ─── check_stale ───────────────────────────────────────────────────────

def test_check_stale_skips_non_git(tmp_path):
    from hydrahive_core.patch_stale_check import check_stale
    f = tmp_path / "lonely.py"
    f.write_text("x = 1")
    res = check_stale(f)
    assert res["stale"] is False
    assert res.get("skipped") == "not_in_git_repo"


def test_check_stale_no_upstream_changes(tmp_path):
    from hydrahive_core.patch_stale_check import check_stale
    _, clone, file = _make_repo_pair(tmp_path)
    res = check_stale(file)
    assert res["stale"] is False, f"got {res}"


def test_check_stale_detects_upstream_changes(tmp_path):
    from hydrahive_core.patch_stale_check import check_stale
    origin, clone, file = _make_repo_pair(tmp_path)
    # zweite Working-Copy macht commit + push
    work2 = tmp_path / "work2"
    _git(["clone", str(origin), str(work2)], tmp_path)
    _git(["config", "user.email", "t@t.t"], work2)
    _git(["config", "user.name", "T"], work2)
    (work2 / "foo.py").write_text("x = 2\n")
    _git(["commit", "-am", "update foo"], work2)
    _git(["push", "origin", "main"], work2)

    res = check_stale(file)
    assert res["stale"] is True, f"got {res}"
    assert res["behind_by"] == 1
    assert len(res["commits"]) == 1
    assert "update foo" in res["commits"][0]


def test_check_stale_only_other_files_changed(tmp_path):
    """Wenn upstream einen anderen File geaendert hat — nicht stale fuer foo.py."""
    from hydrahive_core.patch_stale_check import check_stale
    origin, clone, file = _make_repo_pair(tmp_path)
    work2 = tmp_path / "work2"
    _git(["clone", str(origin), str(work2)], tmp_path)
    _git(["config", "user.email", "t@t.t"], work2)
    _git(["config", "user.name", "T"], work2)
    (work2 / "bar.py").write_text("y = 1\n")
    _git(["add", "."], work2)
    _git(["commit", "-m", "add bar"], work2)
    _git(["push", "origin", "main"], work2)

    res = check_stale(file)
    assert res["stale"] is False, f"foo.py sollte nicht stale sein, got {res}"


def test_stale_response_format():
    from hydrahive_core.patch_stale_check import stale_response
    r = stale_response({
        "stale": True,
        "behind_by": 2,
        "commits": ["abc msg1", "def msg2"],
        "branch": "main",
        "file": "foo.py",
    })
    assert r["ok"] is False
    assert "stale" in r
    assert r["stale"]["behind_by"] == 2
    assert "msg1" in r["hint"] and "msg2" in r["hint"]


def test_check_stale_no_bypass():
    """check_stale hat keinen 'skip'/'force'-Parameter."""
    from hydrahive_core import patch_stale_check
    import inspect
    sig = inspect.signature(patch_stale_check.check_stale)
    for p in sig.parameters:
        assert p not in ("skip", "bypass", "force"), f"bypass-param '{p}'"
