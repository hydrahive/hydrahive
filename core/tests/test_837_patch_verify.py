"""
test_837_patch_verify.py — Gate 1: Auto-Verify post-file_patch (#837).

Sicherstellen dass:
- py_compile-Error erkannt wird
- relevante Tests via AST gefunden werden
- Boss-Workflow nicht umgehbar
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


# ─── py_compile_check ──────────────────────────────────────────────────

def test_py_compile_ok(tmp_path):
    from hydrahive_core.patch_verify import py_compile_check
    f = tmp_path / "good.py"
    f.write_text("x = 1\ndef foo():\n    return x\n")
    res = py_compile_check(f)
    assert res["compile"] == "ok"


def test_py_compile_syntax_error(tmp_path):
    from hydrahive_core.patch_verify import py_compile_check
    f = tmp_path / "bad.py"
    f.write_text("def foo(\n    pass\n")  # unclosed paren
    res = py_compile_check(f)
    assert res["compile"] == "error"
    assert "compile_error" in res
    assert res["compile_error"]  # nicht leer


def test_py_compile_indent_error(tmp_path):
    from hydrahive_core.patch_verify import py_compile_check
    f = tmp_path / "indent.py"
    f.write_text("def foo():\nreturn 1\n")  # missing indent
    res = py_compile_check(f)
    assert res["compile"] == "error"


# ─── Test-Discovery via AST ────────────────────────────────────────────

def test_module_path_simple(tmp_path):
    from hydrahive_core.patch_verify import _module_path_from_file
    repo = tmp_path / "repo"
    src = repo / "core" / "src" / "mypkg"
    src.mkdir(parents=True)
    f = src / "foo.py"
    f.write_text("x=1")
    assert _module_path_from_file(f, repo) == "mypkg.foo"


def test_module_path_init_strips(tmp_path):
    from hydrahive_core.patch_verify import _module_path_from_file
    repo = tmp_path / "repo"
    src = repo / "src" / "mypkg"
    src.mkdir(parents=True)
    f = src / "__init__.py"
    f.write_text("")
    assert _module_path_from_file(f, repo) == "mypkg"


def test_find_related_tests_via_import(tmp_path):
    from hydrahive_core.patch_verify import find_related_tests
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)  # repo-marker
    src = repo / "core" / "src" / "mypkg"
    src.mkdir(parents=True)
    target = src / "foo.py"
    target.write_text("def hello(): return 'hi'\n")

    tests = repo / "core" / "tests"
    tests.mkdir(parents=True)
    matching = tests / "test_foo.py"
    matching.write_text("from mypkg.foo import hello\n\ndef test_h(): assert hello() == 'hi'\n")
    other = tests / "test_other.py"
    other.write_text("def test_x(): assert 1+1 == 2\n")

    found = find_related_tests(target, repo)
    found_names = [p.name for p in found]
    assert "test_foo.py" in found_names
    assert "test_other.py" not in found_names


def test_find_related_tests_no_matches(tmp_path):
    from hydrahive_core.patch_verify import find_related_tests
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    src = repo / "src" / "mypkg"
    src.mkdir(parents=True)
    target = src / "lonely.py"
    target.write_text("def x(): pass\n")
    tests = repo / "tests"
    tests.mkdir(parents=True)
    (tests / "test_unrelated.py").write_text("def test_a(): assert True\n")
    found = find_related_tests(target, repo)
    assert found == []


# ─── verify_patch Integration ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_patch_compile_error(tmp_path):
    from hydrahive_core.patch_verify import verify_patch
    f = tmp_path / "bad.py"
    f.write_text("def x(\n  pass\n")
    res = await verify_patch(f)
    assert res["compile"] == "error"
    assert "compile_error" in res


@pytest.mark.asyncio
async def test_verify_patch_compile_ok_no_repo(tmp_path):
    from hydrahive_core.patch_verify import verify_patch
    f = tmp_path / "ok.py"
    f.write_text("x = 1\n")
    res = await verify_patch(f)
    assert res["compile"] == "ok"
    assert res["scope"]["tests_found"] == 0


@pytest.mark.asyncio
async def test_verify_patch_skips_non_python(tmp_path):
    from hydrahive_core.patch_verify import verify_patch
    f = tmp_path / "config.yaml"
    f.write_text("x: 1\n")
    res = await verify_patch(f)
    assert res["compile"] == "skipped"
    assert res.get("reason") == "not_python"


@pytest.mark.asyncio
async def test_scope_not_suite(tmp_path):
    """scope.tests_failed ist scoped zum gepatchten Modul.
    suite ist leer (verify_patch fuehrt keine Full-Suite aus).
    Zeigt: selbst wenn der Test fehlschlaegt (z.B. Import-Error),
    zeigt scope die richtige Struktur — unabhaengig vom Einzelergebnis."""
    from hydrahive_core.patch_verify import verify_patch

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)

    src = repo / "core" / "src" / "mypkg"
    src.mkdir(parents=True)
    target = src / "foo.py"
    target.write_text("def hello(): return 'hi'\n")

    # Test der target importiert
    tests = repo / "core" / "tests"
    tests.mkdir(parents=True)
    passing = tests / "test_foo.py"
    passing.write_text(
        "from mypkg.foo import hello\n\n"
        "def test_hello(): assert hello() == 'hi'\n"
    )

    res = await verify_patch(target)

    # scope-Struktur vorhanden
    assert "scope" in res
    assert "suite" in res

    # tests_found = 1 weil AST test_foo.py findet
    assert res["scope"]["tests_found"] == 1

    # tests_passed/tests_failed direkt aus pytest-Ergebnis (kann 0 sein
    # wenn Import failt — das zeigt dass scope das echte Ergebnis rappottiert)
    assert isinstance(res["scope"]["tests_passed"], int)
    assert isinstance(res["scope"]["tests_failed"], list)

    # suite bleibt leeres dict (verify_patch kennt keine Suite-Daten)
    assert res["suite"] == {}

    # Deprecated flat aliases sind noch da (Backward-Compat)
    assert "tests_found" in res
    assert "tests_passed" in res
    assert "tests_failed" in res


# ─── Hard-Gate-Probe ───────────────────────────────────────────────────

def test_gate_not_bypassable_by_kwarg():
    """patch_verify ist eine Modul-Funktion ohne 'skip'-Parameter im
    LLM-erreichbaren Tool-Schema. Boss kann verify nicht via Tool-Arg
    skippen — das ist Sinn von Hard-Gate."""
    from hydrahive_core import patch_verify
    import inspect
    sig = inspect.signature(patch_verify.verify_patch)
    # Kein Parameter heisst 'skip' / 'bypass' / 'force'
    for p in sig.parameters:
        assert p not in ("skip", "bypass", "force"), \
            f"patch_verify hat Bypass-Parameter '{p}' — Gate ist nicht hart"
