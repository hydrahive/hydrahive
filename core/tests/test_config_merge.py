"""Tests für merge_agent_config.py (#355)."""
import pytest
import sys
import importlib.util
from pathlib import Path


@pytest.fixture
def merge_module():
    """Import merge_agent_config.py als Modul."""
    spec = importlib.util.spec_from_file_location(
        "merge_agent_config",
        Path(__file__).parent.parent.parent / "scripts" / "merge_agent_config.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_merge_adds_new_tools(merge_module):
    template = {"tools": ["file_read", "new_tool"]}
    runtime = {"tools": ["file_read", "custom_tool"]}
    result = merge_module.merge_config(template, runtime)
    assert "file_read" in result["tools"]
    assert "custom_tool" in result["tools"]
    assert "new_tool" in result["tools"]


def test_merge_keeps_runtime_execution_modes(merge_module):
    template = {"execution_modes": {"default": "safe"}}
    runtime = {"execution_modes": {"default": "unrestricted", "unrestricted": {"permissions": []}}}
    result = merge_module.merge_config(template, runtime)
    assert result["execution_modes"]["default"] == "unrestricted"


def test_merge_uses_template_execution_modes_if_runtime_missing(merge_module):
    template = {"execution_modes": {"default": "safe"}}
    runtime = {}
    result = merge_module.merge_config(template, runtime)
    assert result["execution_modes"]["default"] == "safe"


def test_merge_keeps_runtime_temperature(merge_module):
    template = {"llm": {"model": "claude-sonnet-4-6", "temperature": 0.2}}
    runtime = {"llm": {"model": "old-model", "temperature": 0.8, "max_tokens": 8192}}
    result = merge_module.merge_config(template, runtime)
    assert result["llm"]["model"] == "claude-sonnet-4-6"  # template wins
    assert result["llm"]["temperature"] == 0.8  # runtime wins
    assert result["llm"]["max_tokens"] == 8192  # runtime wins


def test_merge_template_identity_wins(merge_module):
    template = {"identity": "New Identity", "type": "boss"}
    runtime = {"identity": "Old Identity", "type": "specialist"}
    result = merge_module.merge_config(template, runtime)
    assert result["identity"] == "New Identity"
    assert result["type"] == "boss"


def test_merge_keeps_runtime_max_tool_rounds(merge_module):
    template = {"max_tool_rounds": 6}
    runtime = {"max_tool_rounds": 50}
    result = merge_module.merge_config(template, runtime)
    assert result["max_tool_rounds"] == 50


def test_merge_tools_no_duplicates(merge_module):
    tools = merge_module.merge_tools(
        ["a", "b", "c"],
        ["b", "c", "d"],
    )
    assert tools == ["b", "c", "d", "a"]  # runtime first, then new from template
