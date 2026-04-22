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


# =============================================================================
# #847 — Literal-Reflektion: x = 5; assert x == 5
# =============================================================================

def test_lint_rejects_literal_reflection_int():
    """#847: `x = 5; assert x == 5` testet effektiv nichts und muss als
    trivial_assert gemeldet werden."""
    from hydrahive_core.test_lint import lint_test_file
    code = """
def test_x():
    x = 5
    assert x == 5
"""
    v = lint_test_file(code, Path("test_x.py"))
    assert any(e["rule"] == "trivial_assert" for e in v), f"erwartet trivial_assert in {v}"


def test_lint_rejects_literal_reflection_string():
    """#847: gleiches Pattern fuer String-Literal."""
    from hydrahive_core.test_lint import lint_test_file
    code = """
def test_x():
    name = "foo"
    assert name == "foo"
"""
    v = lint_test_file(code, Path("test_x.py"))
    assert any(e["rule"] == "trivial_assert" for e in v), f"erwartet trivial_assert in {v}"


def test_lint_rejects_literal_reflection_reversed():
    """#847: assert 5 == x wo x = 5 vorher — auch mit Konstante auf der
    linken Seite."""
    from hydrahive_core.test_lint import lint_test_file
    code = """
def test_x():
    x = 5
    assert 5 == x
"""
    v = lint_test_file(code, Path("test_x.py"))
    assert any(e["rule"] == "trivial_assert" for e in v), f"erwartet trivial_assert in {v}"


def test_lint_accepts_literal_vs_computed():
    """#847 Gegenprobe: Computed-Value == Literal ist legitim."""
    from hydrahive_core.test_lint import lint_test_file
    code = """
def test_x():
    result = 2 + 2
    assert result == 4
"""
    v = lint_test_file(code, Path("test_x.py"))
    assert not any(e["rule"] == "trivial_assert" for e in v), (
        f"Computed result vs. Literal darf nicht trivial sein: {v}"
    )


def test_lint_accepts_different_literals():
    """#847 Gegenprobe: Literal-Binding mit UNTERSCHIEDLICHEM Compare-Literal
    (Tippfehler-Test) ist kein trivial_assert sondern ein legitimer (falsche)
    assert, der zur Laufzeit scheitern wuerde."""
    from hydrahive_core.test_lint import lint_test_file
    code = """
def test_x():
    x = 5
    assert x == 6
"""
    v = lint_test_file(code, Path("test_x.py"))
    assert not any(e["rule"] == "trivial_assert" for e in v), (
        f"Literal-Mismatch darf nicht trivial sein: {v}"
    )


def test_lint_literal_reflection_respects_rebinding():
    """#847: Wenn x mehrfach neu gebunden wird, gilt der letzte Wert. Gegen-
    beispiel: x=5; x=10; assert x == 10 — das ist Literal-Reflection (x
    bindet zuletzt literal 10, Compare gegen 10)."""
    from hydrahive_core.test_lint import lint_test_file
    code = """
def test_x():
    x = 5
    x = 10
    assert x == 10
"""
    v = lint_test_file(code, Path("test_x.py"))
    assert any(e["rule"] == "trivial_assert" for e in v), (
        f"Re-Binding auf letzten Literal gleich Compare-Literal ist trivial: {v}"
    )
# ─── Rule 1b: hardcoded_path HTTP-URL whitelist (#857) ────────────────

def test_lint_allows_client_post_with_projects_url():
    """#857: client.post("/projects/proj1/memory") ist ein legitimer
    API-Endpunkt, kein hardcoded Systempfad — darf nicht geflaggt werden."""
    from hydrahive_core.test_lint import lint_test_file
    good = '''
import pytest
def test_x():
    client.post("/projects/proj1/memory", json={"key": "value"})
    assert True
'''
    v = lint_test_file(good)
    paths = [x for x in v if x["rule"] == "hardcoded_path"]
    assert not paths, f"client.post URL sollte nicht geflaggt werden: {paths}"


def test_lint_allows_requests_get_with_projects_url():
    """#857: requests.get() als freie Funktion mit /projects/-URL."""
    from hydrahive_core.test_lint import lint_test_file
    good = '''
def test_x():
    r = requests.get("/projects/proj1/memory")
    assert r.status_code == 200
'''
    v = lint_test_file(good)
    paths = [x for x in v if x["rule"] == "hardcoded_path"]
    assert not paths, f"requests.get URL sollte nicht geflaggt werden: {paths}"


def test_lint_rejects_open_with_projects_path():
    """#857: open("/projects/...") ist KEIN HTTP-Client — muss geflaggt werden."""
    from hydrahive_core.test_lint import lint_test_file
    bad = '''
def test_x():
    with open("/projects/hydrahive-coding/repo/data.txt") as f:
        pass
'''
    v = lint_test_file(bad)
    assert any(x["rule"] == "hardcoded_path" for x in v), f"open() sollte geflaggt werden: {v}"


def test_lint_rejects_assert_with_hardcoded_path():
    """#857: assert path == "/projects/foo.py" ist kein HTTP-Call — flag."""
    from hydrahive_core.test_lint import lint_test_file
    bad = '''
def test_x():
    path = "/projects/foo.py"
    assert path == "something_else"
'''
    v = lint_test_file(bad)
    # Genau 1 hardcoded_path violation (nur die Zuweisung)
    paths = [x for x in v if x["rule"] == "hardcoded_path"]
    assert len(paths) == 1 and paths[0]["line"] == 3, (
        f"nur die Zuweisung sollte geflaggt werden: {paths}"
    )