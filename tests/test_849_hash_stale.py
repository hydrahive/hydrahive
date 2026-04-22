"""
Tests for #849: Hash-based Stale-Detection
T1: stale detection when file changed between read→patch
T2: normal patch when file unchanged (hash match)
T3: force_stale bypass
T4: write clears hash state (next patch succeeds without stale)
"""
import asyncio
import hashlib
from pathlib import Path

import pytest

from hydrahive_core.tool_registry import (
    FileReadTool, FilePatchTool, FileWriteTool,
    workspace_root,
)


@pytest.fixture
def agent_id():
    return "test-agent-849"


@pytest.fixture
def file_and_agent(tmp_path, agent_id, monkeypatch):
    """Create a test file inside tmp_path and mock workspace_root to tmp_path."""
    test_file = tmp_path / "notes.txt"
    test_file.write_text("line one\nline two\nline three\n")

    # Mock workspace_root so tools accept paths inside tmp_path
    monkeypatch.setattr(
        "hydrahive_core.tool_registry.workspace_root",
        lambda pid: tmp_path.resolve(),
    )
    # Also mock PROJECTS_ROOT if needed
    monkeypatch.setattr(
        "hydrahive_core.tool_registry.PROJECTS_ROOT",
        tmp_path.parent.parent,
    )

    # Clear all agent state before each test
    FileWriteTool._read_hash_state.clear()
    FileWriteTool._read_state[agent_id] = set()
    FileWriteTool._checkpoints.pop(agent_id, None)

    return test_file, agent_id


class TestHashStaleDetection:
    """T1: File changed between read→patch → stale detected"""

    @pytest.mark.asyncio
    async def test_t1_stale_detection(self, file_and_agent):
        test_file, agent_id = file_and_agent

        # Read file → hash stored
        read_tool = FileReadTool()
        await read_tool.execute(
            agent_id=agent_id, project_id="test-849",
            path=str(test_file), offset=0, limit=100
        )

        # Externally modify file (simulate concurrent change)
        test_file.write_text("line one\nMODIFIED\nline three\n")

        # Attempt patch → should be stale
        patch_tool = FilePatchTool()
        result = await patch_tool.execute(
            agent_id=agent_id, project_id="test-849",
            path=str(test_file), search="line two", replace="CHANGED",
        )

        assert result.get("stale") is True
        assert "#849" in result.get("error", "")

    """T2: File unchanged → patch succeeds normally"""

    @pytest.mark.asyncio
    async def test_t2_normal_patch(self, file_and_agent):
        test_file, agent_id = file_and_agent

        # Read file
        read_tool = FileReadTool()
        await read_tool.execute(
            agent_id=agent_id, project_id="test-849",
            path=str(test_file), offset=0, limit=100
        )

        # Patch without external modification
        patch_tool = FilePatchTool()
        result = await patch_tool.execute(
            agent_id=agent_id, project_id="test-849",
            path=str(test_file), search="line two", replace="CHANGED",
        )

        assert result.get("ok") is True
        assert result.get("replaced") == 1
        content = test_file.read_text()
        assert "CHANGED" in content
        assert "line two" not in content

    """T3: force_stale bypasses stale detection"""

    @pytest.mark.asyncio
    async def test_t3_force_stale_bypass(self, file_and_agent):
        test_file, agent_id = file_and_agent

        # Read file → hash stored
        read_tool = FileReadTool()
        await read_tool.execute(
            agent_id=agent_id, project_id="test-849",
            path=str(test_file), offset=0, limit=100
        )

        # Externally modify file
        test_file.write_text("line one\nMODIFIED\nline three\n")

        # Patch with force_stale=True → should succeed
        patch_tool = FilePatchTool()
        result = await patch_tool.execute(
            agent_id=agent_id, project_id="test-849",
            path=str(test_file), search="line one", replace="CHANGED",
            force_stale=True,
        )

        assert result.get("ok") is True
        assert result.get("replaced") == 1

    """T4: write clears hash state → subsequent patch succeeds without stale"""

    @pytest.mark.asyncio
    async def test_t4_write_clears_hash(self, file_and_agent):
        test_file, agent_id = file_and_agent

        # Read file
        read_tool = FileReadTool()
        await read_tool.execute(
            agent_id=agent_id, project_id="test-849",
            path=str(test_file), offset=0, limit=100
        )

        # Write to same file (simulates a rewrite like overwrite large file)
        write_tool = FileWriteTool()
        new_content = "completely different content\nwith new lines\n"
        await write_tool.execute(
            agent_id=agent_id, project_id="test-849",
            path=str(test_file), content=new_content,
        )

        # Verify hash was cleared (write should have cleared it)
        resolved = str(test_file.resolve())
        stored_hash = FileWriteTool.get_read_hash(resolved)
        assert stored_hash is None, "Hash should be cleared after write"

        # Patch should now succeed without stale error
        # Need to re-read first (normal flow)
        await read_tool.execute(
            agent_id=agent_id, project_id="test-849",
            path=str(test_file), offset=0, limit=100
        )
        patch_tool = FilePatchTool()
        result = await patch_tool.execute(
            agent_id=agent_id, project_id="test-849",
            path=str(test_file), search="completely different", replace="totally different",
        )
        assert result.get("ok") is True, f"Patch failed: {result}"