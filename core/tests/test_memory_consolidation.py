import pytest


@pytest.mark.asyncio
async def test_tool_and_external_api_write_same_project_memory_path(tmp_path, monkeypatch):
    from hydrahive_core import tool_registry
    from hydrahive_core.router_agent_chat import write_agent_memory_file
    from hydrahive_core.tool_registry import WriteMemoryTool

    agents_dir = tmp_path / "agents"
    projects_dir = tmp_path / "projects"
    (agents_dir / "bot").mkdir(parents=True)
    projects_dir.mkdir()

    monkeypatch.setattr(tool_registry, "PROJECTS_ROOT", projects_dir)

    api_resp = write_agent_memory_file(
        agent_id="bot",
        filename="api_note",
        content="from api",
        agents_dir=str(agents_dir),
        projects_dir=str(projects_dir),
    )
    assert api_resp["saved"] is True

    tool_resp = await WriteMemoryTool().execute(
        "bot",
        "bot",
        filename="tool_note",
        content="from tool",
    )
    assert tool_resp["saved"] is True

    memory_dir = projects_dir / "bot" / "memory"
    assert (memory_dir / "api_note.md").read_text(encoding="utf-8") == "from api"
    assert (memory_dir / "tool_note.md").read_text(encoding="utf-8") == "from tool"
    assert not (agents_dir / "bot" / "memory" / "api_note.md").exists()
    assert not (agents_dir / "bot" / "memory" / "tool_note.md").exists()


def test_legacy_agent_memory_migration_merges_to_project_memory(tmp_path, monkeypatch):
    from hydrahive_core import migrations
    from hydrahive_core.settings import settings

    agents_dir = tmp_path / "agents"
    projects_dir = tmp_path / "projects"
    legacy_memory = agents_dir / "bot" / "memory"
    project_memory = projects_dir / "bot" / "memory"
    legacy_memory.mkdir(parents=True)
    project_memory.mkdir(parents=True)

    (legacy_memory / "MEMORY.md").write_text("# Legacy Memory\n", encoding="utf-8")
    (legacy_memory / "facts.md").write_text("legacy facts", encoding="utf-8")
    (project_memory / "INDEX.md").write_text("# Project Index\n", encoding="utf-8")
    (project_memory / "facts.md").write_text("project facts", encoding="utf-8")

    monkeypatch.setattr(settings, "agents_dir", agents_dir)
    monkeypatch.setattr(settings, "projects_dir", projects_dir)

    migrations._m005_consolidate_agent_memory()

    memory_index = (project_memory / "MEMORY.md").read_text(encoding="utf-8")
    assert "# Project Index" in memory_index
    assert "# Legacy Memory" in memory_index
    assert not (project_memory / "INDEX.md").exists()
    assert (project_memory / "facts.md").read_text(encoding="utf-8") == "project facts"
    assert (project_memory / "facts.agent-legacy.md").read_text(encoding="utf-8") == "legacy facts"
    assert not legacy_memory.exists()
