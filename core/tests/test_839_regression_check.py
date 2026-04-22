"""
test_839_regression_check.py — Gate 3: Regression-Test-Gate (#839).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


# ─── find_changed_functions ────────────────────────────────────────────

def test_no_change_returns_empty():
    from hydrahive_core.regression_check import find_changed_functions
    src = "def foo():\n    return 1\n"
    assert find_changed_functions(src, src) == []


def test_body_change_detected():
    from hydrahive_core.regression_check import find_changed_functions
    a = "def foo():\n    return 1\n"
    b = "def foo():\n    return 2\n"
    assert "foo" in find_changed_functions(a, b)


def test_signature_change_detected():
    from hydrahive_core.regression_check import find_changed_functions
    a = "def foo(x):\n    return x\n"
    b = "def foo(x, y):\n    return x + y\n"
    assert "foo" in find_changed_functions(a, b)


def test_new_function_detected():
    from hydrahive_core.regression_check import find_changed_functions
    a = "def foo(): pass\n"
    b = "def foo(): pass\ndef bar(): pass\n"
    assert "bar" in find_changed_functions(a, b)


def test_removed_function_detected():
    from hydrahive_core.regression_check import find_changed_functions
    a = "def foo(): pass\ndef bar(): pass\n"
    b = "def foo(): pass\n"
    assert "bar" in find_changed_functions(a, b)


def test_class_method_detected():
    from hydrahive_core.regression_check import find_changed_functions
    a = "class X:\n    def method(self): return 1\n"
    b = "class X:\n    def method(self): return 2\n"
    assert "X.method" in find_changed_functions(a, b)


def test_docstring_only_change_ignored():
    from hydrahive_core.regression_check import find_changed_functions
    a = 'def foo():\n    """Old doc"""\n    return 1\n'
    b = 'def foo():\n    """New doc"""\n    return 1\n'
    # Docstrings sind weggestripped → kein change
    assert "foo" not in find_changed_functions(a, b)


def test_syntax_error_returns_empty():
    from hydrahive_core.regression_check import find_changed_functions
    assert find_changed_functions("def foo(\n", "def foo(): pass\n") == []


# ─── find_calling_tests ────────────────────────────────────────────────

def test_find_callers_finds_direct_call(tmp_path):
    from hydrahive_core.regression_check import find_calling_tests
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    tests = repo / "core" / "tests"
    tests.mkdir(parents=True)
    t = tests / "test_x.py"
    t.write_text("from mymod import estimate_tokens\n\ndef test_e():\n    assert estimate_tokens('a') == 0\n")
    found = find_calling_tests(["estimate_tokens"], repo)
    assert any(p.name == "test_x.py" for p in found)


def test_find_callers_finds_method_call(tmp_path):
    from hydrahive_core.regression_check import find_calling_tests
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    tests = repo / "tests"
    tests.mkdir(parents=True)
    t = tests / "test_y.py"
    t.write_text("def test_m():\n    obj.do_thing()\n    assert True\n")
    found = find_calling_tests(["X.do_thing"], repo)
    assert any(p.name == "test_y.py" for p in found)


def test_find_callers_no_matches(tmp_path):
    from hydrahive_core.regression_check import find_calling_tests
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    tests = repo / "tests"
    tests.mkdir(parents=True)
    (tests / "test_z.py").write_text("def test_z():\n    assert True\n")
    found = find_calling_tests(["nonexistent_func"], repo)
    assert found == []


# ─── regression_response_addon ─────────────────────────────────────────

def test_addon_skipped_when_no_failure():
    from hydrahive_core.regression_check import regression_response_addon
    res = {"changed_functions": ["foo"], "tests_passed": 5, "tests_failed": []}
    assert regression_response_addon(res) is None


def test_addon_built_when_failures_present():
    from hydrahive_core.regression_check import regression_response_addon
    res = {
        "changed_functions": ["foo"],
        "callers_tested": ["test_foo.py"],
        "tests_passed": 3,
        "tests_failed": ["test_foo.py::test_x"],
        "stdout_tail": "...",
    }
    addon = regression_response_addon(res)
    assert addon is not None
    assert "regression" in addon
    assert addon["regression"]["failed"] == ["test_foo.py::test_x"]
    assert "hint" in addon["regression"]


# ─── No-Bypass ─────────────────────────────────────────────────────────

def test_regression_check_no_bypass():
    from hydrahive_core import regression_check
    import inspect
    sig = inspect.signature(regression_check.regression_check)
    for p in sig.parameters:
        assert p not in ("skip", "bypass", "force"), f"bypass-param '{p}'"
