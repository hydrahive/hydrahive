"""
test_lint.py — Test-Quality-Linter fuer file_write-Hook (#841, Gate 5).

Boss kann keine schlechten Tests mehr ueber file_write committen. Linter
laeuft AUTOMATISCH bei file_write auf test_*.py oder *_test.py — bei
Verstoss: Write reject mit konkreter Violation-Liste.

Vier Regeln:
1. hardcoded_path — String-Literale die auf absolute System-Pfade zeigen,
   ausserhalb von tmp_path/monkeypatch-Kontext
2. no_assert — Test-Funktion ohne assert
3. trivial_assert — assert True / assert x == x / Selbstvergleich
4. self_validating — Expected-Wert wird durch Aufruf der zu testenden
   Funktion gewonnen (assert f(x) == f(x), expected = f(x))

Nicht umgehbar:
- AST-Analyse (kein String-Match), Boss kann keine Comments / String-Tricks nutzen
- file_write hook ist im Tool, nicht im Prompt
- Bei Reject: kein Write, klare Violation-Liste im Tool-Response
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Heuristik: welche Pfad-Praefixe sind "echte System-Pfade"
_FORBIDDEN_PATH_PREFIXES = (
    "/projects/",
    "/home/",
    "/opt/",
    "/etc/",
    "/var/",
    "/root/",
    "/usr/local/",
    # /tmp/ ist erlaubt wenn relativ kurz (random suffix), nicht erlaubt
    # wenn hardcoded statisch (z.B. /tmp/test_venv) — wir flaggen alle
    # /tmp/-Literale die laenger als 5 chars sind und kein "tmp_path"
    # in der Umgebung haben.
)

# HTTP-Methoden fuer Whitelist in _hardcoded_paths (#857)
HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "request"})


def _is_test_file(path: Path) -> bool:
    """Heuristik: ist path eine Pytest-Test-Datei?"""
    name = path.name
    return name.startswith("test_") and name.endswith(".py") or name.endswith("_test.py")


def _collect_test_functions(tree: ast.Module) -> list[ast.FunctionDef]:
    """Findet alle test_* Funktionen — direkt im Modul ODER in Test*-Klassen."""
    funcs: list[ast.FunctionDef] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            funcs.append(node)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith("test_"):
                    funcs.append(sub)
    return funcs


def _has_assert(func: ast.FunctionDef) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            return True
        # pytest.raises kontextmanager auch zaehlen
        if isinstance(node, ast.With):
            for item in node.items:
                if isinstance(item.context_expr, ast.Call):
                    fn = item.context_expr.func
                    if isinstance(fn, ast.Attribute) and fn.attr == "raises":
                        return True
        # method calls die typische assertion-namen sind
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr.startswith("assert"):
                return True
    return False


def _is_trivial_assert(
    node: ast.Assert,
    literal_bindings: dict[str, Any] | None = None,
) -> bool:
    """assert True, assert 1 == 1, assert x == x, assert (...) == (selber-call).

    #847: mit `literal_bindings` (map var-name -> Konstantenwert, gesammelt aus
    vorausgehenden ast.Assign-Knoten) wird auch erkannt:
        x = 5
        assert x == 5      # Literal-Reflektion: x bindet literal, Compare ist
                           # exakt dieselbe Konstante.
    """
    test = node.test
    # assert True / assert 1 / assert <constant truthy>
    if isinstance(test, ast.Constant):
        if test.value is True or (isinstance(test.value, (int, float)) and test.value):
            return True
    # assert x == x oder assert obj.x == obj.x
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        if isinstance(test.ops[0], ast.Eq):
            left = ast.dump(test.left)
            right = ast.dump(test.comparators[0])
            if left == right:
                return True
            # #847: Literal-Reflektion — Name == Constant wo Name vorher
            # genau dieser Constant zugewiesen wurde.
            if literal_bindings:
                left_val = _resolve_literal_binding(test.left, literal_bindings)
                right_val = _resolve_literal_binding(test.comparators[0], literal_bindings)
                if (
                    left_val is not _SENTINEL
                    and right_val is not _SENTINEL
                    and type(left_val) == type(right_val)
                    and left_val == right_val
                ):
                    return True
    return False


# Sentinel-Wert fuer "nicht auflösbar" — wir koennen None nicht verwenden weil
# None selbst ein legitimer Constant-Wert ist.
_SENTINEL = object()


def _resolve_literal_binding(
    expr: ast.expr,
    literal_bindings: dict[str, Any],
) -> Any:
    """#847: Wenn expr ein ast.Name ist der auf einen Literal gebunden wurde,
    gib den Literal-Wert zurueck. Wenn expr selbst ein ast.Constant ist, gib
    dessen Wert zurueck. Sonst _SENTINEL."""
    if isinstance(expr, ast.Constant):
        return expr.value
    if isinstance(expr, ast.Name) and expr.id in literal_bindings:
        return literal_bindings[expr.id]
    return _SENTINEL


def _collect_literal_bindings(func: ast.FunctionDef) -> dict[str, Any]:
    """#847: Sammelt einfache `name = <Constant>`-Bindungen innerhalb einer
    Funktion. Re-Bindungen ueberschreiben. Konservativ: nur direktes
    `x = 42`, keine Tupel-Assigns, keine Attribute, keine Subscripts.
    """
    bindings: dict[str, Any] = {}
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
        ):
            bindings[node.targets[0].id] = node.value.value
    return bindings


def _self_validating_assert(func: ast.FunctionDef) -> tuple[bool, str]:
    """Erkennt Pattern wo expected aus dem zu testenden Call selbst kommt:
        result = func(x)
        expected = func(x)   # SAME call
        assert result == expected
    Oder direkt: assert func(x) == func(x).
    """
    # Sammle alle Assignments + Asserts
    assignments: dict[str, str] = {}  # var_name → ast_dump des assigned value
    for node in ast.walk(func):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                assignments[node.targets[0].id] = ast.dump(node.value)
            except Exception:
                pass
    for node in ast.walk(func):
        if isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare):
            if len(node.test.ops) == 1 and isinstance(node.test.ops[0], ast.Eq):
                left = node.test.left
                right = node.test.comparators[0]
                # Pattern A: assert func(x) == func(x) — direkter Self-Call
                try:
                    if ast.dump(left) == ast.dump(right):
                        return True, "assert <call> == <selber call>"
                except Exception:
                    pass
                # Pattern B: assert var1 == var2 wo beide auf gleichen Call assigned
                if isinstance(left, ast.Name) and isinstance(right, ast.Name):
                    l_val = assignments.get(left.id)
                    r_val = assignments.get(right.id)
                    if l_val and r_val and l_val == r_val and "Call" in l_val:
                        return True, f"assert {left.id} == {right.id} (beide aus gleichem Call)"
    return False, ""


def _build_parent_map(tree: ast.Module) -> dict[ast.AST, ast.AST]:
    """Erzeugt Child→Parent Map fuer alle Knoten im Baum (fuer #857 whitelist)."""
    parent: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    return parent


def _is_http_call_arg(node: ast.Constant, parent_map: dict[ast.AST, ast.AST]) -> bool:
    """#857: True wenn node ein String-Argument zu einer HTTP-Methoden-Client-
    methode ist (client.post, requests.get, etc.). Solche URLs sind legitime
    API-Endpunkte, keine hardcoded Systempfade."""
    parent = parent_map.get(node)
    if not isinstance(parent, ast.Call):
        return False
    func = parent.func
    if isinstance(func, ast.Attribute) and func.attr in HTTP_METHODS:
        return True
    if isinstance(func, ast.Name) and func.id in HTTP_METHODS:
        return True
    return False


def _hardcoded_paths(tree: ast.Module) -> list[tuple[int, str]]:
    """Findet String-Literale die auf absolute Pfade zeigen.
    Whitelist: HTTP-Client-Aufrufe mit /projects/- URLs (API-Endpunkte, #857)
    werden nicht geflaggt.
    """
    hits: list[tuple[int, str]] = []
    parent_map = _build_parent_map(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            # leerer / sehr kurzer String → ignore
            if len(v) < 5:
                continue
            # Whitelist: HTTP-Client-URLs (#857)
            if _is_http_call_arg(node, parent_map):
                continue
            for prefix in _FORBIDDEN_PATH_PREFIXES:
                if v.startswith(prefix):
                    hits.append((node.lineno, v[:80]))
                    break
            else:
                # /tmp/-Literal mit hartem Suffix
                if v.startswith("/tmp/") and len(v) > 5 and not v.endswith("/"):
                    suffix = v[5:]
                    # tmp_path-fixture liefert Pfade wie /tmp/pytest-of-USER/pytest-N/test_name0
                    # die werden NICHT als Literal geschrieben — Boss-Pfade sind statisch
                    if "/" not in suffix or not suffix.startswith("pytest-"):
                        hits.append((node.lineno, v[:80]))
    return hits


# ─── Linter-Hauptfunktion ──────────────────────────────────────────────


def lint_test_file(content: str, path: Path | None = None) -> list[dict[str, Any]]:
    """Lintet den Inhalt einer Test-Datei. Returnt Liste von Violations.

    Jede Violation:
      {"rule": "hardcoded_path" | "no_assert" | "trivial_assert" | "self_validating",
       "line": int,
       "match": str,
       "function": str (optional, fuer no_assert/trivial/self_validating)}

    Leere Liste wenn alles OK.
    """
    violations: list[dict[str, Any]] = []
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        # Syntax-Fehler ist kein Lint-Issue (py_compile fängt das via Gate 1)
        # Hier: nur returnen damit der Linter nicht crashed
        return [{
            "rule": "syntax_error",
            "line": e.lineno or 0,
            "match": str(e),
        }]

    # 1. hardcoded paths
    for line_no, snippet in _hardcoded_paths(tree):
        violations.append({
            "rule": "hardcoded_path",
            "line": line_no,
            "match": snippet,
        })

    # 2/3/4 — pro Test-Funktion
    test_funcs = _collect_test_functions(tree)
    for func in test_funcs:
        # 2. no_assert
        if not _has_assert(func):
            violations.append({
                "rule": "no_assert",
                "line": func.lineno,
                "function": func.name,
                "match": f"def {func.name}(...) hat kein assert/raises",
            })
            continue  # weitere Checks irrelevant ohne assert
        # 3. trivial assert (#847: inkl. Literal-Reflektion x=5; assert x==5)
        _lit_bindings = _collect_literal_bindings(func)
        for node in ast.walk(func):
            if isinstance(node, ast.Assert) and _is_trivial_assert(node, _lit_bindings):
                violations.append({
                    "rule": "trivial_assert",
                    "line": node.lineno,
                    "function": func.name,
                    "match": "assert True / x == x / x=5; assert x==5 / aehnlich",
                })
                break
        # 4. self-validating
        is_self, reason = _self_validating_assert(func)
        if is_self:
            violations.append({
                "rule": "self_validating",
                "line": func.lineno,
                "function": func.name,
                "match": reason,
            })

    return violations


def lint_response(violations: list[dict[str, Any]], path: Path | None = None) -> dict[str, Any]:
    """Formatiert Violations als Tool-Response-dict mit hint."""
    if not violations:
        return {"ok": True, "violations": []}
    return {
        "ok": False,
        "lint": {
            "path": str(path) if path else None,
            "violations": violations,
            "hint": (
                "Test-Quality-Issues — Write rejected. "
                "Hardcoded /projects/, /home/ etc.: nutze tmp_path-Fixture oder monkeypatch. "
                "Kein assert: jede test_*-Funktion braucht mindestens ein assert oder pytest.raises. "
                "Trivial: 'assert True' testet nichts. "
                "Self-validating: Expected-Wert darf nicht durch denselben Aufruf entstehen — "
                "nutze Literal-Werte fuer Expected."
            ),
        },
    }
