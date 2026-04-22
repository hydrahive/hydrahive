"""
patch_verify.py — Auto-Verify-Hook fuer file_patch / file_write (#837, Gate 1).

Kern-Gate der Boss-Disziplin. Der Boss kann nicht mehr "fertig" sagen
ohne dass der Patch compile-getestet + gegen existierende Tests gelaufen ist.

Enforcement-Prinzip:
- .py-Datei geaendert → subprocess py_compile; bei SyntaxError: Caller muss
  revert ausloesen. Hier returnen wir {compile: "error", compile_error: ...}.
- Tests die diese Datei importieren werden via AST gefunden und mit pytest
  ausgefuehrt. Ergebnis im return dict. Kein Auto-Revert bei Test-Fail —
  der Boss SIEHT dass Tests rot sind, muss selbst darauf reagieren.

Nicht umgehbar, weil:
- Der Return-Dict ist Teil des Tool-Responses den das LLM liest
- py_compile + pytest laufen als subprocess — nicht vom LLM steuerbar
- Revert-Trigger ist im Tool, nicht im Prompt
"""
from __future__ import annotations

import asyncio
import ast
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─── py_compile ────────────────────────────────────────────────────────

def py_compile_check(file_path: Path) -> dict[str, Any]:
    """Blocking py_compile. Kurze timeout weil nur Parse.

    Returns:
      {"compile": "ok"} wenn erfolgreich
      {"compile": "error", "compile_error": "<msg>"} bei SyntaxError / ImportError-bei-parse
    """
    try:
        r = subprocess.run(
            [sys.executable, "-m", "py_compile", str(file_path)],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        return {"compile": "error", "compile_error": "py_compile timeout (15s)"}
    except FileNotFoundError:
        return {"compile": "skipped", "reason": "python3 nicht verfuegbar"}

    if r.returncode == 0:
        return {"compile": "ok"}
    return {
        "compile": "error",
        "compile_error": (r.stderr or r.stdout or "unknown")[:500],
    }


async def py_compile_check_async(file_path: Path) -> dict[str, Any]:
    """Async-Wrapper fuer py_compile_check."""
    return await asyncio.to_thread(py_compile_check, file_path)


# ─── Test-Discovery via AST ────────────────────────────────────────────

def _module_path_from_file(file_path: Path, repo_root: Path) -> str | None:
    """Fuer /repo/core/src/hydrahive_core/foo.py → 'hydrahive_core.foo'.

    Heuristik: 'src'-Verzeichnis als Package-Root ansehen wenn vorhanden.
    Nur bei .py-Dateien sinnvoll.
    """
    if file_path.suffix != ".py":
        return None
    try:
        rel = file_path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return None
    parts = list(rel.parts)
    # Wenn 'src' im Pfad: ab dort slicen
    if "src" in parts:
        parts = parts[parts.index("src") + 1:]
    if not parts:
        return None
    # .py entfernen vom letzten Teil
    parts[-1] = parts[-1][:-3]
    # __init__.py → nur package-name
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return None
    return ".".join(parts)


def find_related_tests(file_path: Path, repo_root: Path) -> list[Path]:
    """Findet Test-Dateien die das Modul von file_path importieren.

    Via AST-Scan aller test_*.py / *_test.py im repo_root/tests/ und
    repo_root/core/tests/ (falls core-Subdir-Layout).

    Returnt Liste absoluter Pfade, max. 20 um Laufzeit zu bounden.
    """
    module = _module_path_from_file(file_path, repo_root)
    if not module:
        return []
    # Such-Roots
    candidates: list[Path] = []
    for sub in ("tests", "core/tests"):
        d = repo_root / sub
        if d.exists():
            for p in d.rglob("test_*.py"):
                candidates.append(p)
            for p in d.rglob("*_test.py"):
                candidates.append(p)
    # Dedup
    seen: set[str] = set()
    unique_candidates: list[Path] = []
    for p in candidates:
        s = str(p.resolve())
        if s not in seen:
            seen.add(s)
            unique_candidates.append(p)
    # Match: Import-Statement enthaelt module?
    matches: list[Path] = []
    module_head = module.split(".")[0]
    for p in unique_candidates:
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and (node.module == module or node.module.startswith(module + ".") or node.module == module_head or node.module.startswith(module_head + ".")):
                    matches.append(p)
                    break
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == module or alias.name == module_head or alias.name.startswith(module_head + "."):
                        matches.append(p)
                        break
                else:
                    continue
                break
        if len(matches) >= 20:
            break
    return matches


# ─── pytest-Runner ─────────────────────────────────────────────────────

def _find_pytest_exe() -> str | None:
    """Pytest-Binary suchen — Core-venv bevorzugt, dann PATH."""
    # 1. Core-venv
    for candidate in (
        Path("/opt/hydrahive/venv/bin/pytest"),
        Path(sys.prefix) / "bin" / "pytest",
    ):
        if candidate.exists():
            return str(candidate)
    # 2. PATH
    w = shutil.which("pytest")
    if w:
        return w
    # 3. python -m pytest als Fallback
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "--version"],
            capture_output=True, timeout=5,
        )
        if r.returncode == 0:
            return f"{sys.executable} -m pytest"
    except Exception:
        pass
    return None


def run_tests(test_paths: list[Path], repo_root: Path) -> dict[str, Any]:
    """Fuehrt pytest auf einer Liste von Test-Files aus.

    Returnt:
      {
        "ran": True/False,
        "passed": int,
        "failed": [test_id, ...],  # max 10
        "stdout_tail": "..."         # letzte ~500 chars fuer LLM
      }
    oder bei nichts-zu-tun:
      {"ran": False, "reason": "no_tests_found"}
    oder bei fehlender pytest-Installation:
      {"ran": False, "reason": "pytest_not_found"}
    """
    if not test_paths:
        return {"ran": False, "reason": "no_tests_found"}
    pytest_exe = _find_pytest_exe()
    if not pytest_exe:
        return {"ran": False, "reason": "pytest_not_found"}

    cmd: list[str]
    if " " in pytest_exe:
        cmd = pytest_exe.split() + ["-q", "--no-header"]
    else:
        cmd = [pytest_exe, "-q", "--no-header"]
    # Pfade relativ zum repo_root fuer lesbare Output
    for p in test_paths:
        try:
            rel = p.resolve().relative_to(repo_root.resolve())
            cmd.append(str(rel))
        except ValueError:
            cmd.append(str(p))
    try:
        r = subprocess.run(
            cmd,
            cwd=str(repo_root),
            capture_output=True, text=True, timeout=90,
        )
    except subprocess.TimeoutExpired:
        return {"ran": False, "reason": "timeout_90s"}

    stdout = r.stdout or ""
    stderr = r.stderr or ""
    # Pytest-Exit-Codes: 0=pass, 1=fail, 2=interrupted, 3=internal, 4=cmdline, 5=no-tests
    if r.returncode == 5:
        return {"ran": False, "reason": "no_tests_collected"}

    # Parse Pytest-Output fuer failed tests
    failed: list[str] = []
    passed = 0
    for line in stdout.splitlines():
        if line.startswith("FAILED "):
            # "FAILED tests/test_foo.py::test_bar - AssertionError..."
            after = line[len("FAILED "):].split(" - ", 1)[0].strip()
            if after and len(failed) < 10:
                failed.append(after)
        # "N passed, M failed in X.XXs"
        for tok in line.split(","):
            tok = tok.strip()
            if tok.endswith("passed"):
                try:
                    passed = int(tok.split()[0])
                except (ValueError, IndexError):
                    pass

    tail = (stdout + ("\n" + stderr if stderr.strip() else ""))[-500:]
    return {
        "ran": True,
        "passed": passed,
        "failed": failed,
        "stdout_tail": tail,
    }


async def run_tests_async(test_paths: list[Path], repo_root: Path) -> dict[str, Any]:
    return await asyncio.to_thread(run_tests, test_paths, repo_root)


# ─── Repo-Root-Finder ──────────────────────────────────────────────────

def find_repo_root(path: Path) -> Path | None:
    """Sucht aufwaerts nach .git-Dir. None wenn nicht gefunden."""
    try:
        p = path.resolve()
    except OSError:
        return None
    for ancestor in [p] + list(p.parents):
        if (ancestor / ".git").exists():
            return ancestor
    return None


# ─── Haupt-Verify ──────────────────────────────────────────────────────

async def verify_patch(file_path: Path, *, run_tests_flag: bool = True) -> dict[str, Any]:
    """Kombinierter Verify-Hook fuer file_patch / file_write.

    Args:
      file_path: die geaenderte Datei
      run_tests_flag: wenn False, ueberspringt Test-Lauf (nur py_compile)

    Returns:
      {
        "compile": "ok" | "error" | "skipped",
        "compile_error": ... (nur bei error),
        "scope": {
          "test_files_found": int,  # scoped test FILES via AST
          "tests_passed": int,       # passed test CASES from pytest
          "tests_failed": [...],
        },
        "suite": {},              # verify_patch fuellt nur scope; leeres dict
        "stdout_tail": str,
        # Deprecated flat aliases (bleiben fuer Backward-Compat):
        "tests_found": int,        # deprecated — alias for scope["test_files_found"]
      }
    """
    result: dict[str, Any] = {}
    # Nur .py relevant fuer compile + tests
    if file_path.suffix != ".py":
        result["compile"] = "skipped"
        result["reason"] = "not_python"
        result["scope"] = {"test_files_found": 0, "tests_passed": 0, "tests_failed": []}
        result["suite"] = {}
        result["tests_found"] = 0
        result["tests_passed"] = 0
        result["tests_failed"] = []
        return result

    compile_res = await py_compile_check_async(file_path)
    result.update(compile_res)
    # Bei Compile-Error: kein Test-Lauf
    if compile_res.get("compile") == "error":
        result["scope"] = {"test_files_found": 0, "tests_passed": 0, "tests_failed": []}
        result["suite"] = {}
        result["tests_found"] = 0
        result["tests_passed"] = 0
        result["tests_failed"] = []
        return result

    if not run_tests_flag:
        result["scope"] = {"test_files_found": 0, "tests_passed": 0, "tests_failed": []}
        result["suite"] = {}
        result["tests_found"] = 0
        result["tests_passed"] = 0
        result["tests_failed"] = []
        return result

    repo_root = find_repo_root(file_path)
    if repo_root is None:
        result["scope"] = {"test_files_found": 0, "tests_passed": 0, "tests_failed": []}
        result["suite"] = {}
        result["tests_skipped_reason"] = "no_git_repo"
        result["tests_found"] = 0
        result["tests_passed"] = 0
        result["tests_failed"] = []
        return result

    related = find_related_tests(file_path, repo_root)
    scope = {"test_files_found": len(related), "tests_passed": 0, "tests_failed": []}
    result["scope"] = scope
    result["suite"] = {}
    result["tests_found"] = len(related)
    if not related:
        result["tests_passed"] = 0
        result["tests_failed"] = []
        return result

    test_res = await run_tests_async(related, repo_root)
    if test_res.get("ran"):
        scope["tests_passed"] = test_res.get("passed", 0)
        scope["tests_failed"] = test_res.get("failed", [])
        result["tests_passed"] = test_res.get("passed", 0)
        result["tests_failed"] = test_res.get("failed", [])
        result["stdout_tail"] = test_res.get("stdout_tail", "")
    else:
        result["tests_skipped_reason"] = test_res.get("reason", "unknown")
    return result
