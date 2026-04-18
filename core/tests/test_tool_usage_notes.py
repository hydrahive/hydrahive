"""#713: Tool-Usage-Notes gegen token-teure Arbeitsmuster."""
from __future__ import annotations

from hydrahive_core.tool_registry import (
    AskAgentTool,
    FilePatchTool,
    FileReadTool,
    FileSearchTool,
    FileWriteTool,
    ReadMemoryTool,
    ShellExecTool,
    WriteMemoryTool,
)


def test_core_tool_usage_notes_are_present():
    expected = {
        ShellExecTool: ("file_read", "nicht cat/head/tail", "bündeln"),
        FileReadTool: ("file_search", "Grep ist billiger"),
        FileWriteTool: ("Nur nach file_read", "file_patch"),
        FilePatchTool: ("file_write vorziehen", "exakt aus file_read"),
        FileSearchTool: ("Primär-Werkzeug", "vor file_read"),
        ReadMemoryTool: ("MEMORY.md-Index", "1-2 Dateien"),
        WriteMemoryTool: ("Ein Eintrag pro Thema", "MEMORY.md-Index"),
        AskAgentTool: ("breite Recherche", "nicht für triviale Einzelfragen"),
    }

    for tool_cls, needles in expected.items():
        description = tool_cls().description
        for needle in needles:
            assert needle in description, f"{tool_cls.__name__} fehlt Usage-Note: {needle}"
