"""Test #850: file_read clamp note — Agents sehen warum limit geaendert wurde."""

import pytest
import sys
from pathlib import Path
from unittest.mock import patch

# Ensure project src is on path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from hydrahive_core.tool_registry import FileReadTool


class TestFileReadClampNote:
    """#850: When limit is clamped, result should contain a `note` field."""

    @pytest.mark.asyncio
    async def test_clamp_below_minimum_adds_note(self, tmp_path, monkeypatch):
        """limit=60 should be clamped to 4000 with a note explaining why."""
        test_file = tmp_path / "tiny.txt"
        test_file.write_text("Hello world from tiny file!\n", encoding="utf-8")

        with patch("hydrahive_core.tool_registry.assert_path_within_project", return_value=test_file):
            tool = FileReadTool()
            result = await tool.execute(
                agent_id="test-agent",
                project_id="test-project",
                path="tiny.txt",
                offset=0,
                limit=60,
            )

        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        assert "note" in result, f"Expected 'note' field when limit is clamped. Got: {result}"
        note = result["note"]
        assert "60" in note, f"Note should contain original value '60'. Got: {note}"
        assert "4000" in note, f"Note should contain clamped value '4000'. Got: {note}"

    @pytest.mark.asyncio
    async def test_clamp_above_maximum_adds_note(self, tmp_path, monkeypatch):
        """limit=99999 should be clamped to 32000 with a note explaining why."""
        test_file = tmp_path / "tiny.txt"
        test_file.write_text("Hello world from tiny file!\n", encoding="utf-8")

        with patch("hydrahive_core.tool_registry.assert_path_within_project", return_value=test_file):
            tool = FileReadTool()
            result = await tool.execute(
                agent_id="test-agent",
                project_id="test-project",
                path="tiny.txt",
                offset=0,
                limit=99999,
            )

        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        assert "note" in result, f"Expected 'note' field when limit is clamped. Got: {result}"
        note = result["note"]
        assert "99999" in note, f"Note should contain original value '99999'. Got: {note}"
        assert "32000" in note, f"Note should contain clamped value '32000'. Got: {note}"

    @pytest.mark.asyncio
    async def test_no_note_when_limit_is_in_range(self, tmp_path, monkeypatch):
        """limit=8000 is within range — no note field should be present."""
        test_file = tmp_path / "tiny.txt"
        test_file.write_text("Hello world from tiny file!\n", encoding="utf-8")

        with patch("hydrahive_core.tool_registry.assert_path_within_project", return_value=test_file):
            tool = FileReadTool()
            result = await tool.execute(
                agent_id="test-agent",
                project_id="test-project",
                path="tiny.txt",
                offset=0,
                limit=8000,
            )

        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        assert "note" not in result, f"No 'note' expected when limit is in range. Got: {result.get('note')}"
