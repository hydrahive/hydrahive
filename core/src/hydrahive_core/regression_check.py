"""
regression_check.py — Gate 3: Regression-Test-Gate (#839).

Verhindert dass Boss eine Funktion aendert ohne dass die Tests die diese
Funktion aufrufen gegen den neuen Code laufen.

Workflow:
1. Vor Patch: original-Content speichern (vom Caller geliefert)
2. Nach Patch: AST-Diff ueber beide Versionen → changed_functions
3. find_callers: scannt test_*.py nach AST-Calls auf changed_functions
4. pytest auf gefundene Tests → wenn rot, Tool-Response enthaelt regression-Block

Ergaenzt patch_verify (Gate 1) um den Aspekt "Tests die NICHT direkt im
Modul-Import-Graph sind, aber den geaenderten Funktionsnamen aufrufen".
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _func_signature_dump(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Stable hash der Funktion (args + body, ohne docstrings/comments)."""
    # Body ohne fuhrenden docstring
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    args_dump = ast.dump(node.args)
    body_dump = "\n".join(ast.dump(n) for n in body)
    return hashlib.sha256(f"{args_dump}|{body_dump}".encode()).hexdigest()


def _collect_functions(tree: ast.Module) -> dict[str, str]:
    """Map function-name → stable hash. Inkl. methods aus top-level Klassen."""
    funcs: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs[node.name] = _func_signature_dump(node)
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    funcs[f"{node.name}.{sub.name}"] = _func_signature_dump(sub)
    return funcs


def find_changed_functions(original_src: str, patched_src: str) -> list[str]:
    """Diff: welche Funktionen haben sich zwischen original und patched
    geaendert (body oder args)? Returnt deren Namen (top-level oder
    Class.method).

    Bei SyntaxError in einer Version: returnt []. Caller muss compile-
    Check separat machen (Gate 1).
    """
    try:
        orig_tree = ast.parse(original_src)
        patch_tree = ast.parse(patched_src)
    except SyntaxError:
        return []
    orig_funcs = _collect_functions(orig_tree)
    patch_funcs = _collect_functions(patch_tree)
    changed: list[str] = []
    # Geaenderte: in beiden, aber unterschiedlicher Hash
    for name, h in orig_funcs.items():
        if name in patch_funcs and patch_funcs[name] != h:
            changed.append(name)
    # Neue: nur in patched
    for name in patch_funcs:
        if name not in orig_funcs:
            changed.append(name)
    # Entfernte: nur in original
    for name in orig_funcs:
        if name not in patch_funcs:
            changed.append(name)
    return changed


# ─── find_callers ──────────────────────────────────────────────────────


def _function_calls_in_file(file_content: str) -> set[str]:
    """Sammelt alle aufgerufenen Funktions/Methoden-Namen.
    Returnt Set von 'foo' und 'X.bar'."""
    calls: set[str] = set()
    try:
        tree = ast.parse(file_content)
    except SyntaxError:
        return calls
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                calls.add(f.id)
            elif isinstance(f, ast.Attribute):
                # method call. Wir zählen nur den Method-Namen + den
                # Attribute-Stamm wenn moeglich
                calls.add(f.attr)
                if isinstance(f.value, ast.Name):
                    calls.add(f"{f.value.id}.{f.attr}")
    return calls


def find_calling_tests(changed_functions: list[str], repo_root: Path) -> list[Path]:
    """Findet Test-Dateien deren Code Aufrufe auf changed_functions enthaelt.

    Heuristik: scannt test_*.py / *_test.py unter tests/ und core/tests/.
    Bei Class.method-Form wird auch der bare-method-Name geprueft.
    """
    if not changed_functions:
        return []
    # Sammle alle gesuchten Namen — auch nur den Method-Teil bei "X.method"
    needles: set[str] = set()
    for fn in changed_functions:
        needles.add(fn)
        if "." in fn:
            needles.add(fn.split(".", 1)[1])  # nur Method-Teil
    candidates: list[Path] = []
    for sub in ("tests", "core/tests"):
        d = repo_root / sub
        if d.exists():
            for p in d.rglob("test_*.py"):
                candidates.append(p)
            for p in d.rglob("*_test.py"):
                candidates.append(p)
    seen: set[str] = set()
    matches: list[Path] = []
    for p in candidates:
        s = str(p.resolve())
        if s in seen:
            continue
        seen.add(s)
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        calls = _function_calls_in_file(content)
        if calls & needles:
            matches.append(p)
        if len(matches) >= 30:
            break
    return matches


# ─── Haupt-Check ───────────────────────────────────────────────────────


async def regression_check(
    file_path: Path,
    original_src: str,
    patched_src: str,
    *,
    pytest_timeout: int = 90,
) -> dict[str, Any]:
    """Fuehrt Regression-Check aus. Returns dict mit:
      {"changed_functions": [...], "callers_tested": [...],
       "tests_failed": [...], "tests_passed": int, "skipped": "<reason>"}
    """
    result: dict[str, Any] = {}

    if file_path.suffix != ".py":
        return {"skipped": "not_python"}

    changed = find_changed_functions(original_src, patched_src)
    result["changed_functions"] = changed
    if not changed:
        return result

    # Repo-Root finden
    from .patch_verify import find_repo_root, run_tests_async
    repo_root = find_repo_root(file_path)
    if repo_root is None:
        result["skipped"] = "no_git_repo"
        return result

    callers = find_calling_tests(changed, repo_root)
    result["callers_tested"] = [str(p.relative_to(repo_root)) for p in callers]
    if not callers:
        return result

    # pytest auf Caller
    test_res = await run_tests_async(callers, repo_root)
    if test_res.get("ran"):
        result["tests_passed"] = test_res.get("passed", 0)
        result["tests_failed"] = test_res.get("failed", [])
        result["stdout_tail"] = test_res.get("stdout_tail", "")
    else:
        result["skipped"] = f"pytest_{test_res.get('reason', 'unknown')}"
    return result


def regression_response_addon(reg_result: dict[str, Any]) -> dict[str, Any] | None:
    """Wenn Regression detektiert, returnt ein Tool-Response-Addon dict.
    Sonst None (kein addon noetig)."""
    failed = reg_result.get("tests_failed") or []
    if not failed:
        return None
    return {
        "regression": {
            "changed_functions": reg_result.get("changed_functions", []),
            "callers_tested": reg_result.get("callers_tested", []),
            "failed": failed,
            "stdout_tail": reg_result.get("stdout_tail", ""),
            "hint": (
                "Patch hat existierende Tests gebrochen die diese Funktion(en) "
                "aufrufen. Entweder Patch korrigieren oder Tests an neues "
                "Verhalten anpassen — aber ehrlich kommunizieren dass es eine "
                "Verhaltensaenderung war."
            ),
        }
    }
