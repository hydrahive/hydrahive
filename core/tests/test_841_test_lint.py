"""
test_841_test_lint.py — Gate 5: Test-Quality-Linter (#841).

Verifiziert dass schlechte Tests beim file_write rejected werden.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ─── Rule 1: hardcoded_path ────────────────────────────────────────────

def test_lint_rejects_hardcoded_projects_path():
    from hydrahive_core.test_lint import lint_test_file
    bad = '''
import pytest
def test_x():
    p = "/projects/hydrahive-coding/repo/foo.py"
    assert p
'''
    v = lint_test_file(bad)
    assert any(x["rule"] == "hardcoded_path" for x in v), f"got: {v}"


def test_lint_rejects_hardcoded_home_path():
    from hydrahive_core.test_lint import lint_test_file
    bad = '''
def test_x():
    assert "/home/till/octopos" != ""
'''
    v = lint_test_file(bad)
    assert any(x["rule"] == "hardcoded_path" for x in v)


def test_lint_allows_tmp_path_fixture():
    from hydrahive_core.test_lint import lint_test_file
    good = '''
import pytest
def test_x(tmp_path):
    f = tmp_path / "foo.txt"
    f.write_text("hi")
    assert f.read_text() == "hi"
'''
    v = lint_test_file(good)
    assert not any(x["rule"] == "hardcoded_path" for x in v), f"unexpected violations: {v}"


def test_lint_allows_short_strings():
    from hydrahive_core.test_lint import lint_test_file
    good = '''
def test_x():
    assert "/" == "/"
'''
    v = lint_test_file(good)
    # "/" ist 1 char, kein violation. trivial assert wird aber gefangen!
    paths = [x for x in v if x["rule"] == "hardcoded_path"]
    assert not paths


# ─── Rule 2: no_assert ─────────────────────────────────────────────────

def test_lint_rejects_test_without_assert():
    from hydrahive_core.test_lint import lint_test_file
    bad = '''
def test_does_nothing():
    x = 1 + 1
    print(x)
'''
    v = lint_test_file(bad)
    assert any(x["rule"] == "no_assert" for x in v)


def test_lint_accepts_pytest_raises_as_assert():
    from hydrahive_core.test_lint import lint_test_file
    good = '''
import pytest
def test_raises():
    with pytest.raises(ValueError):
        raise ValueError("x")
'''
    v = lint_test_file(good)
    assert not any(x["rule"] == "no_assert" for x in v), f"got {v}"


# ─── Rule 3: trivial_assert ────────────────────────────────────────────

def test_lint_rejects_assert_true():
    from hydrahive_core.test_lint import lint_test_file
    bad = '''
def test_x():
    assert True
'''
    v = lint_test_file(bad)
    assert any(x["rule"] == "trivial_assert" for x in v)


def test_lint_rejects_self_compare():
    from hydrahive_core.test_lint import lint_test_file
    bad = '''
def test_x():
    x = 5
    assert x == x
'''
    v = lint_test_file(bad)
    assert any(x["rule"] == "trivial_assert" for x in v)


# ─── Rule 4: self_validating ───────────────────────────────────────────

def test_lint_rejects_self_validating_via_var():
    from hydrahive_core.test_lint import lint_test_file
    bad = '''
def my_function(x):
    return x * 2

def test_x():
    result = my_function(3)
    expected = my_function(3)
    assert result == expected
'''
    v = lint_test_file(bad)
    assert any(x["rule"] == "self_validating" for x in v), f"got {v}"


def test_lint_rejects_direct_self_call():
    from hydrahive_core.test_lint import lint_test_file
    bad = '''
def f(x): return x

def test_x():
    assert f(5) == f(5)
'''
    v = lint_test_file(bad)
    assert any(x["rule"] == "self_validating" for x in v) or any(x["rule"] == "trivial_assert" for x in v)


def test_lint_accepts_literal_expected():
    from hydrahive_core.test_lint import lint_test_file
    good = '''
def my_function(x):
    return x * 2

def test_x():
    assert my_function(3) == 6
'''
    v = lint_test_file(good)
    assert not v, f"unexpected violations: {v}"


# ─── _is_test_file ─────────────────────────────────────────────────────

def test_is_test_file_recognizes_test_prefix():
    from hydrahive_core.test_lint import _is_test_file
    assert _is_test_file(Path("test_foo.py"))
    assert _is_test_file(Path("/x/y/test_bar.py"))


def test_is_test_file_recognizes_test_suffix():
    from hydrahive_core.test_lint import _is_test_file
    assert _is_test_file(Path("foo_test.py"))


def test_is_test_file_rejects_normal_python():
    from hydrahive_core.test_lint import _is_test_file
    assert not _is_test_file(Path("foo.py"))
    assert not _is_test_file(Path("test.py"))


# ─── Hard-Gate-Probe ───────────────────────────────────────────────────

def test_lint_response_format():
    from hydrahive_core.test_lint import lint_response
    v = [{"rule": "no_assert", "line": 5, "function": "test_x", "match": "..."}]
    r = lint_response(v, Path("/tmp/test_foo.py"))
    assert r["ok"] is False
    assert "lint" in r
    assert r["lint"]["violations"] == v
    assert "hint" in r["lint"]


def test_lint_no_bypass_kwarg():
    """lint_test_file hat keinen 'skip' / 'bypass'-Parameter."""
    from hydrahive_core.test_lint import lint_test_file
    import inspect
    sig = inspect.signature(lint_test_file)
    for p in sig.parameters:
        assert p not in ("skip", "bypass", "force"), f"bypass-Param '{p}'"
