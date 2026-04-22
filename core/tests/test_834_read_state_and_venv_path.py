"""
test_834_read_state_and_venv_path.py — Bug #834: Zwei Fixes
1) file_write registriert Pfad in _read_state → file_patch danach klappt ohne file_read
2) _resolve_sandbox_scope baut .venv/bin in PATH ein wenn .venv existiert
"""
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.tool_registry import (
    FileWriteTool,
    FilePatchTool,
    _resolve_sandbox_scope,
    workspace_root,
)


# ============================================================================
# Fix 1: file_write registriert Pfad in _read_state
# ============================================================================

def test_file_write_registers_path_in_read_state(tmp_path, monkeypatch):
    """
    Nach einem erfolgreichen file_write wird der aufgelöste Pfad in
    FileWriteTool._read_state für den Agenten eingetragen.
    """
    FileWriteTool._read_state.clear()
    FileWriteTool._checkpoints.clear()

    monkeypatch.setattr(
        "hydrahive_core.tool_registry.workspace_root",
        lambda pid: tmp_path if pid == "test_proj" else Path(f"/projects/{pid}"),
    )

    tool = FileWriteTool()
    project_id = "test_proj"
    agent_id = "agent_834_1"

    test_file = tmp_path / "written_by_me.txt"

    async def run():
        return await tool.execute(
            agent_id=agent_id,
            project_id=project_id,
            path=str(test_file),
            content="Hello world",
            mode="overwrite",
        )

    result = asyncio.run(run())
    assert result.get("written") is True, f"write failed: {result}"

    read_set = FileWriteTool._read_state.get(agent_id, set())
    assert str(test_file.resolve()) in read_set, \
        f"file_write hat Pfad nicht in _read_state eingetragen. Got: {read_set}"


def test_file_patch_after_file_write_without_file_read(tmp_path, monkeypatch):
    """
    Regression #834: file_write erzeugt Datei → sofortiges file_patch
    darauf darf NICHT mit "Read-Before-Edit" fehlschlagen.
    """
    FileWriteTool._read_state.clear()
    FileWriteTool._checkpoints.clear()

    monkeypatch.setattr(
        "hydrahive_core.tool_registry.workspace_root",
        lambda pid: tmp_path if pid == "test_proj" else Path(f"/projects/{pid}"),
    )

    write_tool = FileWriteTool()
    patch_tool = FilePatchTool()
    project_id = "test_proj"
    agent_id = "agent_834_2"

    test_file = tmp_path / "patchable.txt"
    original = "line one\nline two\nline three\n"

    async def write_and_patch():
        wr = await write_tool.execute(
            agent_id=agent_id,
            project_id=project_id,
            path=str(test_file),
            content=original,
            mode="overwrite",
        )
        pr = await patch_tool.execute(
            agent_id=agent_id,
            project_id=project_id,
            path=str(test_file),
            search="line two",
            replace="line TWO",
            count=1,
        )
        return wr, pr

    wr, pr = asyncio.run(write_and_patch())

    assert wr.get("written") is True, f"write failed: {wr}"

    # Read-Before-Edit darf NICHT auftreten
    assert "error" not in pr or "Read-Before-Edit" not in str(pr.get("error", "")), \
        f"file_patch failed with Read-Before-Edit despite file_write: {pr}"

    # Patch war erfolgreich
    assert pr.get("ok") is True, f"patch failed: {pr}"
    assert pr.get("replaced", 0) == 1, f"patch replaced != 1: {pr}"

    # Inhalt korrekt?
    final = test_file.read_text()
    assert "line TWO" in final
    assert "line two" not in final


# ============================================================================
# Fix 2: _resolve_sandbox_scope enthält .venv/bin im PATH
# ============================================================================

def test_resolve_sandbox_scope_includes_venv_bin_in_path(tmp_path, monkeypatch):
    """
    Wenn eine .venv im workspace_root existiert, wird deren bin/-Ordner
    an den PATH-String in den bwrap-Argumenten angehängt.
    """
    venv_dir = tmp_path / ".venv"
    venv_bin = venv_dir / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").touch()

    def fake_workspace_root(project_id):
        return tmp_path

    monkeypatch.setattr(
        "hydrahive_core.tool_registry.workspace_root",
        fake_workspace_root,
    )

    project_id = "proj_with_venv"
    _, bind_args = _resolve_sandbox_scope(project_id, "/tmp")

    path_env = None
    for i, arg in enumerate(bind_args):
        if arg == "--setenv" and i + 2 < len(bind_args):
            if bind_args[i + 1] == "PATH":
                path_env = bind_args[i + 2]
                break

    assert path_env is not None, f"PATH nicht in bind_args gefunden: {bind_args}"
    assert str(venv_bin) in path_env, \
        f".venv/bin nicht in PATH. Got: {path_env}"


def test_resolve_sandbox_scope_path_without_venv(tmp_path, monkeypatch):
    """
    Wenn keine .venv existiert, bleibt der Default-PATH erhalten —
    .venv/bin ist NICHT darin (kein false-positive).
    """
    def fake_workspace_root(project_id):
        return tmp_path

    monkeypatch.setattr(
        "hydrahive_core.tool_registry.workspace_root",
        fake_workspace_root,
    )

    project_id = "proj_no_venv"
    _, bind_args = _resolve_sandbox_scope(project_id, "/tmp")

    path_env = None
    for i, arg in enumerate(bind_args):
        if arg == "--setenv" and i + 2 < len(bind_args):
            if bind_args[i + 1] == "PATH":
                path_env = bind_args[i + 2]
                break

    assert path_env is not None
    assert ".venv" not in path_env, f"False positive: .venv in PATH: {path_env}"

