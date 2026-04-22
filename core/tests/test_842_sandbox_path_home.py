"""
test_842_sandbox_path_home.py — Gate 6: Sandbox-Fix (#842).

Verifiziert dass:
- _resolve_sandbox_scope baut PATH mit Core-venv-bin + Projekt-venv + System
- HOME ist persistent in project_dir/.home statt /tmp
- PYTHONUSERBASE wird gesetzt damit pip --user persistent funktioniert
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


def _scope_for(project_id: str | None, cwd: Path, monkeypatch, tmp_path):
    """Helper: ruft _resolve_sandbox_scope und gibt die bind_args zurueck."""
    from hydrahive_core import tool_registry
    monkeypatch.setattr(tool_registry, "PROJECTS_ROOT", tmp_path)
    return tool_registry._resolve_sandbox_scope(project_id, cwd)


def _get_setenv(bind_args: list[str], var: str) -> str | None:
    """Liest --setenv VAR VALUE aus den bind_args."""
    for i, a in enumerate(bind_args):
        if a == "--setenv" and i + 1 < len(bind_args) and bind_args[i + 1] == var:
            return bind_args[i + 2] if i + 2 < len(bind_args) else None
    return None


def test_sandbox_home_persistent_for_project(tmp_path, monkeypatch):
    """HOME ist <project_dir>/.home wenn Projekt existiert."""
    pid = "myproj"
    proj = tmp_path / pid
    proj.mkdir()
    cwd, args = _scope_for(pid, proj, monkeypatch, tmp_path)
    home = _get_setenv(args, "HOME")
    assert home == str(proj / ".home"), f"HOME={home}"
    # .home Verzeichnis wurde angelegt
    assert (proj / ".home").exists()


def test_sandbox_home_falls_back_to_tmp_without_project(tmp_path, monkeypatch):
    """Ohne project_id: HOME bleibt /tmp."""
    cwd, args = _scope_for(None, Path("/tmp"), monkeypatch, tmp_path)
    home = _get_setenv(args, "HOME")
    assert home == "/tmp"


def test_sandbox_path_includes_system(tmp_path, monkeypatch):
    """System-PATH ist immer am Ende drin."""
    pid = "p"
    proj = tmp_path / pid
    proj.mkdir()
    cwd, args = _scope_for(pid, proj, monkeypatch, tmp_path)
    path = _get_setenv(args, "PATH")
    assert "/usr/bin" in path
    assert "/bin" in path


def test_sandbox_path_includes_project_venv_when_present(tmp_path, monkeypatch):
    """Projekt-eigenes .venv/bin landet im PATH wenn vorhanden."""
    pid = "p"
    proj = tmp_path / pid
    venv_bin = proj / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    cwd, args = _scope_for(pid, proj, monkeypatch, tmp_path)
    path = _get_setenv(args, "PATH")
    assert str(venv_bin) in path


def test_sandbox_path_excludes_project_venv_when_missing(tmp_path, monkeypatch):
    """Wenn kein .venv im Projekt: nicht im PATH."""
    pid = "p"
    proj = tmp_path / pid
    proj.mkdir()  # kein .venv
    cwd, args = _scope_for(pid, proj, monkeypatch, tmp_path)
    path = _get_setenv(args, "PATH")
    assert ".venv/bin" not in path


def test_sandbox_pythonuserbase_set(tmp_path, monkeypatch):
    """PYTHONUSERBASE = <home>/.local damit pip --user persistent ist."""
    pid = "p"
    proj = tmp_path / pid
    proj.mkdir()
    cwd, args = _scope_for(pid, proj, monkeypatch, tmp_path)
    pub = _get_setenv(args, "PYTHONUSERBASE")
    expected = str(proj / ".home" / ".local")
    assert pub == expected


def test_sandbox_core_venv_mounted_ro(tmp_path, monkeypatch):
    """/opt/hydrahive/venv wird ro-gemountet (try) — damit pytest aus Core-venv
    erreichbar ist."""
    pid = "p"
    proj = tmp_path / pid
    proj.mkdir()
    cwd, args = _scope_for(pid, proj, monkeypatch, tmp_path)
    # Pattern '--ro-bind-try /opt/hydrahive/venv /opt/hydrahive/venv' im args
    assert args.count("/opt/hydrahive/venv") == 2  # source + target
    # finde den --ro-bind-try der zur Core-venv gehoert
    found = False
    for i in range(len(args) - 2):
        if (args[i] == "--ro-bind-try"
            and args[i + 1] == "/opt/hydrahive/venv"
            and args[i + 2] == "/opt/hydrahive/venv"):
            found = True
            break
    assert found, "Kein --ro-bind-try Pattern fuer Core-venv"
