"""
test_tool_guard.py — #717 harter ToolGuard

Deckt die in #717 geforderten Fälle ab:
- read-only Commands in /projects/hydrahivedev/repo erlaubt
- `git status` etc. in stale checkout erlaubt
- `git commit` / `git push` in stale checkout blockiert
- `file_patch` / `file_write` in stale checkout blockiert
- `file_patch` / `file_write` in /home/till/octopos erlaubt
- `shell_exec` mit legitimer Schreibaktion in Canonical-Checkout erlaubt
- fehlende / defekte Config → sichere Defaults
- Block-Message nennt canonical_path, keine Secrets
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core import tool_guard as tg
from hydrahive_core.tool_guard import (
    ToolGuardDecision,
    check_tool_guard,
    reset_config_cache,
)


STALE_REPO = "/projects/hydrahivedev/repo"
STALE_FILE = f"{STALE_REPO}/core/src/hydrahive_core/orchestrator_context.py"
CANONICAL = "/home/till/octopos"
CANONICAL_FILE = f"{CANONICAL}/core/src/hydrahive_core/orchestrator_context.py"


@pytest.fixture(autouse=True)
def _isolate_config(monkeypatch, tmp_path):
    """Für jeden Test frische Config-Lage: Config-Pfad zeigt auf nicht-
    existierendes tmp-File — damit Defaults greifen und keine Host-Config
    die Tests beeinflusst."""
    monkeypatch.setattr(tg, "_CONFIG_PATH", tmp_path / "tool_guard.json")
    reset_config_cache()
    yield
    reset_config_cache()


# ─────────────────────────────────────────────── read-only in stale erlaubt

@pytest.mark.parametrize("cmd", [
    "ls -la",
    "cat README.md",
    "rg --files",
    "grep foo bar.py",
    "sed -n '1,20p' file.py",
    "git status",
    "git log --oneline -5",
    "git diff HEAD~1",
    "git remote -v",
    "git branch",
    "pwd",
    "find . -name '*.py' -maxdepth 2",
])
def test_readonly_commands_allowed_in_stale(cmd):
    dec = check_tool_guard("shell_exec", {"command": cmd, "cwd": STALE_REPO})
    assert dec.allowed, f"{cmd!r} sollte in stalem Checkout erlaubt sein (Diagnose)"
    assert dec.code in ("", "read_in_stale", "guard_disabled")


# ─────────────────────────────────────────────── write in stale blockiert

@pytest.mark.parametrize("cmd", [
    "git add .",
    "git commit -m 'x'",
    "git push origin main",
    "git reset --hard HEAD~1",
    "git checkout -- .",
    "git clean -fd",
    "git rebase main",
    "git pull --rebase",
    "rm -rf build",
    "mv a b",
    "cp a b",
    "echo foo | tee out.txt",
    "echo foo > out.txt",
    "sed -i 's/a/b/' file.py",
    "npm install",
    "pnpm run build",
    "yarn build",
    "python3 -c \"open('x','w').write('y')\"",
])
def test_write_commands_blocked_in_stale(cmd):
    dec = check_tool_guard("shell_exec", {"command": cmd, "cwd": STALE_REPO})
    assert not dec.allowed, f"{cmd!r} muss in stalem Checkout blockiert werden"
    assert dec.code == "write_in_stale_checkout"
    assert CANONICAL in dec.message, "Block-Message muss kanonischen Pfad nennen"


def test_git_commit_explicit_contract():
    dec = check_tool_guard("shell_exec", {
        "command": "git commit -m 'feat: test'",
        "cwd": STALE_REPO,
    })
    assert not dec.allowed
    assert dec.detected_path == STALE_REPO
    assert dec.canonical_path == CANONICAL
    assert "git commit" in (dec.hint or "")


def test_git_push_explicit_contract():
    dec = check_tool_guard("shell_exec", {
        "command": "git push hydrahive main",
        "cwd": STALE_REPO,
    })
    assert not dec.allowed
    assert dec.code == "write_in_stale_checkout"


# ─────────────────────────────────────────────── stale auch über Command-Substring

def test_stale_path_in_command_triggers_block_even_without_cwd():
    dec = check_tool_guard("shell_exec", {
        "command": f"rm {STALE_REPO}/old.txt",
        "cwd": "/tmp",
    })
    assert not dec.allowed
    assert dec.detected_path == STALE_REPO


# ─────────────────────────────────────────────── file_write / file_patch

def test_file_patch_blocked_in_stale():
    dec = check_tool_guard("file_patch", {
        "path": STALE_FILE,
        "search": "foo",
        "replace": "bar",
    })
    assert not dec.allowed
    assert dec.code == "write_in_stale_checkout"
    assert CANONICAL in dec.message


def test_file_write_blocked_in_stale():
    dec = check_tool_guard("file_write", {
        "path": STALE_FILE,
        "content": "x",
    })
    assert not dec.allowed
    assert dec.detected_path == STALE_REPO


def test_file_patch_allowed_in_canonical():
    dec = check_tool_guard("file_patch", {
        "path": CANONICAL_FILE,
        "search": "foo",
        "replace": "bar",
    })
    assert dec.allowed


def test_file_write_allowed_in_canonical():
    dec = check_tool_guard("file_write", {
        "path": CANONICAL_FILE,
        "content": "x",
    })
    assert dec.allowed


def test_server_file_write_blocked_in_stale():
    dec = check_tool_guard("server_file_write", {
        "server_id": "foo",
        "path": STALE_FILE,
        "content": "x",
    })
    assert not dec.allowed


def test_file_read_always_allowed_even_in_stale():
    dec = check_tool_guard("file_read", {"path": STALE_FILE})
    assert dec.allowed


# ─────────────────────────────────────────────── shell_exec in Canonical

def test_shell_exec_write_in_canonical_allowed():
    dec = check_tool_guard("shell_exec", {
        "command": "git commit -m 'feat: x'",
        "cwd": CANONICAL,
    })
    assert dec.allowed, "Writes im kanonischen Checkout sind erlaubt"


def test_shell_exec_without_cwd_and_without_stale_path_allowed():
    dec = check_tool_guard("shell_exec", {
        "command": "ls /tmp",
    })
    assert dec.allowed


# ─────────────────────────────────────────────── Config-Handling

def test_missing_config_falls_back_to_defaults(monkeypatch, tmp_path):
    cfg_path = tmp_path / "does_not_exist.json"
    monkeypatch.setattr(tg, "_CONFIG_PATH", cfg_path)
    reset_config_cache()
    dec = check_tool_guard("shell_exec", {
        "command": "git commit -m x",
        "cwd": STALE_REPO,
    })
    assert not dec.allowed
    assert dec.canonical_path == CANONICAL


def test_broken_config_falls_back_to_defaults(monkeypatch, tmp_path):
    cfg = tmp_path / "tool_guard.json"
    cfg.write_text("this-is-not-json{{", encoding="utf-8")
    monkeypatch.setattr(tg, "_CONFIG_PATH", cfg)
    reset_config_cache()
    dec = check_tool_guard("shell_exec", {
        "command": "git commit -m x",
        "cwd": STALE_REPO,
    })
    assert not dec.allowed, "Defekte Config darf Guard nicht abschalten"
    assert dec.canonical_path == CANONICAL


def test_config_can_disable_guard_explicitly(monkeypatch, tmp_path):
    cfg = tmp_path / "tool_guard.json"
    cfg.write_text('{"enabled": false}', encoding="utf-8")
    monkeypatch.setattr(tg, "_CONFIG_PATH", cfg)
    reset_config_cache()
    dec = check_tool_guard("shell_exec", {
        "command": "git commit -m x",
        "cwd": STALE_REPO,
    })
    assert dec.allowed
    assert dec.code == "guard_disabled"


def test_config_custom_stale_roots(monkeypatch, tmp_path):
    cfg = tmp_path / "tool_guard.json"
    cfg.write_text('{"stale_write_roots": ["/srv/legacy"]}', encoding="utf-8")
    monkeypatch.setattr(tg, "_CONFIG_PATH", cfg)
    reset_config_cache()
    # Default-Stale-Root ist jetzt NICHT mehr aktiv → commit in /projects/... erlaubt
    dec = check_tool_guard("shell_exec", {
        "command": "git commit -m x",
        "cwd": STALE_REPO,
    })
    assert dec.allowed
    # /srv/legacy stattdessen aktiv
    dec2 = check_tool_guard("shell_exec", {
        "command": "git commit -m x",
        "cwd": "/srv/legacy/foo",
    })
    assert not dec2.allowed


def test_config_canonical_path_overrides_matching_stale_root(monkeypatch, tmp_path):
    cfg = tmp_path / "tool_guard.json"
    cfg.write_text(
        '{"canonical_path": "/srv/current", "stale_write_roots": ["/srv/current", "/srv/old"]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(tg, "_CONFIG_PATH", cfg)
    reset_config_cache()
    dec = check_tool_guard("shell_exec", {
        "command": "git commit -m x",
        "cwd": "/srv/current",
    })
    assert dec.allowed
    dec2 = check_tool_guard("shell_exec", {
        "command": "git commit -m x",
        "cwd": "/srv/old",
    })
    assert not dec2.allowed


def test_wks_shell_exec_blocked_in_stale():
    dec = check_tool_guard("wks_shell_exec", {
        "command": "git commit -m x",
        "cwd": STALE_REPO,
    })
    assert not dec.allowed


def test_git_bisect_is_write_in_stale():
    dec = check_tool_guard("shell_exec", {
        "command": "git bisect start",
        "cwd": STALE_REPO,
    })
    assert not dec.allowed


# ─────────────────────────────────────────────── Block-Nachricht / Hygiene

def test_block_message_mentions_canonical_path_not_secrets():
    dec = check_tool_guard("shell_exec", {
        "command": "git commit -m x",
        "cwd": STALE_REPO,
    })
    assert not dec.allowed
    assert CANONICAL in dec.message
    # Keine offensichtlichen Secrets in der Nachricht (Token-Pattern etc.)
    for bad in ("ghp_", "password", "token=", "BEGIN RSA", "api_key"):
        assert bad not in dec.message.lower() or bad == "token="  # paranoider Check


def test_decision_type():
    dec = check_tool_guard("file_read", {"path": "/tmp/x"})
    assert isinstance(dec, ToolGuardDecision)
    assert hasattr(dec, "allowed") and hasattr(dec, "canonical_path")


# ─────────────────────────────────────────────── zusätzliche stale roots

def test_opt_hydrahive_core_is_stale():
    dec = check_tool_guard("file_write", {
        "path": "/opt/hydrahive/core/src/foo.py",
        "content": "x",
    })
    assert not dec.allowed


def test_home_octopos_hydrahive_is_stale():
    dec = check_tool_guard("shell_exec", {
        "command": "git commit -m x",
        "cwd": "/home/octopos/hydrahive",
    })
    assert not dec.allowed


# ─────────────────────────────────────────────── unknown tool → allow-through

def test_unknown_tool_name_allowed():
    dec = check_tool_guard("some_future_tool", {"foo": "bar"})
    assert dec.allowed


def test_invalid_tool_input_shape_safe():
    dec = check_tool_guard("shell_exec", None)  # type: ignore[arg-type]
    assert dec.allowed, "Null-Input darf nicht abstürzen"


# ─────────────────────────────────────────────── #759 python open() mode-sensitiv

from hydrahive_core.tool_guard import _classify_python_opens


@pytest.mark.parametrize("rest_str,expected", [
    # read-modes
    ("open('f').read()", "read"),
    ("open('f', 'r').read()", "read"),
    ("open('f', 'rb').read()", "read"),
    ("open('f', 'rt').read()", "read"),
    ("open('f', 'br').read()", "read"),
    ('open("/etc/hostname").read()', "read"),
    # write-modes
    ("open('f', 'w').write('x')", "write"),
    ("open('f', 'a').close()", "write"),
    ("open('f', 'x')", "write"),
    ("open('f', 'wb').write(b'')", "write"),
    ("open('f', 'r+').read()", "write"),  # r+ ist auch write
    ("open('f', 'ab')", "write"),
    # Unparseable Mode → safer write
    ("open(f, mode)", "write"),  # Variable, keine Literal-Quotes
    ("open('f', '')", "write"),  # leerer mode
    # multi-open: ein write → write
    ("open('a').read(); open('b', 'w').write('x')", "write"),
    # multi-open: alle read → read
    ("x = open('a').read(); y = open('b', 'rb').read()", "read"),
    # kein open → None
    ("print('hello')", None),
    ("subprocess.run(['ls'])", None),
])
def test_classify_python_opens(rest_str, expected):
    assert _classify_python_opens(rest_str) == expected


@pytest.mark.parametrize("cmd", [
    "python3 -c \"print(open('/etc/hostname').read())\"",
    "python3 -c \"data = open('f', 'r').read()\"",
    "python -c \"open('f', 'rb').read()\"",
])
def test_python_open_readmode_allowed_in_stale(cmd):
    """#759: reine open()-read Commands dürfen im stale Checkout laufen."""
    dec = check_tool_guard("shell_exec", {"command": cmd, "cwd": STALE_REPO})
    assert dec.allowed, f"{cmd!r} ist read-only, darf in stale nicht blocken"


@pytest.mark.parametrize("cmd", [
    "python3 -c \"open('x','w').write('y')\"",  # bestehend, bleibt write
    "python -c \"open('f', 'a').close()\"",
    "python3 -c \"open('f', 'r+').write('x')\"",
    "python -c \"open('f', 'wb').write(b'')\"",
    "python3 -c \"open(f, mode).write('x')\"",  # unparseable → safer write
])
def test_python_open_writemode_blocked_in_stale(cmd):
    """#759: open()-writes bleiben im stale Checkout blockiert."""
    dec = check_tool_guard("shell_exec", {"command": cmd, "cwd": STALE_REPO})
    assert not dec.allowed, f"{cmd!r} schreibt, muss in stale blockiert sein"
    assert dec.code == "write_in_stale_checkout"


def test_python_explicit_write_method_still_blocks():
    """`.write_text` wird weiter über Pattern-Liste gefangen (vor open-Check)."""
    dec = check_tool_guard("shell_exec", {
        "command": "python3 -c \"Path('f').write_text('x')\"",
        "cwd": STALE_REPO,
    })
    assert not dec.allowed
    assert dec.code == "write_in_stale_checkout"


def test_python_no_open_falls_through_to_safer_write():
    """Python-Script ohne open() und ohne .write → Fallback `write`."""
    dec = check_tool_guard("shell_exec", {
        "command": "python3 script.py",
        "cwd": STALE_REPO,
    })
    assert not dec.allowed, "Unbekannter Script-Aufruf bleibt safer=write"
