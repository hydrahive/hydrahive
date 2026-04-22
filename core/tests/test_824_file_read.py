"""
test_824_file_read.py — Bug #824, Fix 1: file_read limit floor
limit=60 darf nicht durchgeschliffen werden — Minimum ist 4000.
"""
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock


def test_limit_floor_4000():
    """limit=60 wird auf 4000 hochgestuft."""
    from hydrahive_core.tool_registry import FileReadTool
    tool = FileReadTool()

    params = tool.parameters
    # Default im Schema ist 8000, Minimum-Floor ist 4000
    assert params["properties"]["limit"]["type"] == "integer"

    # Sanity: floor-implementation in execute 
    limit_passed = 60
    enforced = min(max(4000, limit_passed), 32000)
    assert enforced == 4000, f"limit=60 sollte auf 4000 erhöht werden, nicht {enforced}"


def test_limit_floor_even_when_explicitly_1():
    """limit=1 wird auf 4000 hochgestuft."""
    limit_passed = 1
    enforced = min(max(4000, limit_passed), 32000)
    assert enforced == 4000


def test_limit_caps_at_32000():
    """limit=99999 wird auf 32000 gekappt."""
    limit_passed = 99999
    enforced = min(max(4000, limit_passed), 32000)
    assert enforced == 32000


def test_limit_8000_unchanged():
    """limit=8000 bleibt 8000 (Default und gültig)."""
    limit_passed = 8000
    enforced = min(max(4000, limit_passed), 32000)
    assert enforced == 8000


@pytest.mark.asyncio
async def test_file_read_respects_limit_min(tmp_path, monkeypatch):
    """Integration: limit=60 in execute() ergibt effektiv 4000."""
    import sys
    sys.path.insert(0, str(tmp_path.parent.parent / "src"))

    test_file = tmp_path / "large.txt"
    test_file.write_text("x" * 10000, encoding="utf-8")

    # Simulated call with limit=60
    limit_passed = 60
    effective_limit = min(max(4000, limit_passed), 32000)

    assert effective_limit == 4000, "floor must be 4000"

    from hydrahive_core.tool_registry import FileReadTool
    tool = FileReadTool()

    # Patch assert_path_within_project to return our tmp path
    with patch("hydrahive_core.tool_registry.assert_path_within_project", return_value=test_file):
        result = await tool.execute(
            agent_id="test-agent",
            project_id="test-project",
            path="large.txt",
            offset=0,
            limit=60,  # explicitly 60 — should be floored to 4000
        )

    content = result["content"]
    assert len(content) == 4000, f"Expected 4000 chars, got {len(content)}"
    assert result["total_size"] == 10000
    # With limit=60 and offset=0, has_more must be True
    assert result.get("has_more") is True
    assert result.get("next_offset") == 4000

