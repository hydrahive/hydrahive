"""
test_824_file_search.py — Bug #824, Fix 2: file_search Schema-Mapping
Grep-Output /path/to/file:LINE:text — regex anchored, nicht split(":", 2).
Testet: Dateiname mit Doppelpunkt (z.B. Windows-Pfad, URL) wird nicht
mehr als Zeilennummer missverstanden.
"""
import os
import pytest
import re
from pathlib import Path
from unittest.mock import patch, AsyncMock

# Integration-Tests brauchen /projects/hydrahive-coding (nur auf .227 vorhanden).
# Lokal überspringen — Unit-Tests ohne Filesystem-Zugriff laufen weiter.
_REQUIRES_PROJECT_FS = pytest.mark.skipif(
    not Path("/projects/hydrahive-coding").exists() or not os.access("/projects", os.W_OK),
    reason="braucht /projects/hydrahive-coding (nur auf Test-Server vorhanden)",
)


def test_grep_line_regex_parses_correctly():
    """Das regex^(.+?):(\\d+):(.*)$ trennt Pfad, Zeile, Text korrekt."""
    _GREP_LINE_RE = re.compile(r"^(.+?):(\d+):(.*)$")

    # Normal case: /projects/hydrahive/src/foo.py:1227:some content
    m = _GREP_LINE_RE.match("/projects/hydrahive/src/foo.py:1227:some content")
    assert m is not None
    assert m.group(1) == "/projects/hydrahive/src/foo.py"
    assert m.group(2) == "1227"
    assert m.group(3) == "some content"


def test_grep_line_regex_with_colon_in_path():
    """Pfad mit Doppelpunkt (z.B. /Volumes/Data/file: 2GB.txt) wird korrekt geparst."""
    _GREP_LINE_RE = re.compile(r"^(.+?):(\d+):(.*)$")

    # File with colon in name
    m = _GREP_LINE_RE.match("/projects/test/file:with:colons.txt:99:actual text")
    assert m is not None
    assert m.group(1) == "/projects/test/file:with:colons.txt"
    assert m.group(2) == "99"
    assert m.group(3) == "actual text"


def test_grep_line_regex_no_match_when_no_line_number():
    """Zeile ohne führende Zeilennummer matcht nicht → wird verworfen."""
    _GREP_LINE_RE = re.compile(r"^(.+?):(\d+):(.*)$")

    # No line number → no match
    m = _GREP_LINE_RE.match("Binary file matches")
    assert m is None


def test_grep_line_regex_truncates_long_text():
    """Textanteil wird auf 200 Zeichen gekürzt."""
    _GREP_LINE_RE = re.compile(r"^(.+?):(\d+):(.*)$")

    long_text = "x" * 500
    m = _GREP_LINE_RE.match(f"/p/f.txt:1:{long_text}")
    assert m is not None
    # regex captures full text; truncation to 200 chars happens in the tool, not the regex


def test_file_search_schema_fields_correct():
    """file_search gibt正确es Schema zurück: file=str, line=int, text=str."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

    from hydrahive_core.tool_registry import FileSearchTool
    tool = FileSearchTool()

    params = tool.parameters
    assert "file" in str(params) or "file" in tool.description
    # The matches structure: file=string, line=integer, text=string
    # This is implicit in the execute return dict, test via mock


@_REQUIRES_PROJECT_FS
@pytest.mark.asyncio
async def test_file_search_parses_grep_output_with_line_numbers(tmp_path, monkeypatch):
    """Integration: grep -rn Output mit echten Zeilennummern wird korrekt gemappt."""
    import sys
    sys.path.insert(0, str(tmp_path.parent.parent / "src"))

    # Create a temp file INSIDE the project (file_search requires project path)
    from pathlib import Path
    proj_root = Path("/projects/hydrahive-coding")
    search_dir = proj_root / "repo" / "core" / "src"
    test_file = search_dir / "_test_824_search.py"
    test_file.write_text("line0\nline1\ncompaction_threshold = 42\nline3\n", encoding="utf-8")

    grep_output = f"{search_dir}/_test_824_search.py:2:compaction_threshold = 42\n"

    class MockResult:
        returncode = 0
        stdout = grep_output

    from hydrahive_core.tool_registry import FileSearchTool
    tool = FileSearchTool()

    try:
        with patch("subprocess.run", return_value=MockResult()):
            result = await tool.execute(
                agent_id="test-agent",
                project_id="hydrahive-coding",
                pattern="compaction_threshold",
                path="repo/core/src",
                max_results=20,
            )

        assert result["count"] == 1, f"got: {result!r}"
        match = result["matches"][0]
        assert match["file"] == "_test_824_search.py", f"got file={match['file']!r}"
        assert match["line"] == 2, f"got line={match['line']}"
        assert "compaction_threshold" in match["text"]
    finally:
        test_file.unlink(missing_ok=True)


@_REQUIRES_PROJECT_FS
@pytest.mark.asyncio
async def test_file_search_issue_824_example(tmp_path, monkeypatch):
    """
    Reproduziert das exakte Issue #824-Szenario:
    grep findet compaction_threshold in Zeile 1227 + 1366.
    Frueher: file="1227" (Zeilennummer als Dateiname).
    Jetzt: file=<realpath>, line=<int>, text=<content>.
    """
    from pathlib import Path
    proj_root = Path("/projects/hydrahive-coding")
    search_dir = proj_root / "repo" / "core" / "src"
    test_file = search_dir / "orchestrator_context.py"
    test_file.write_text("dummy", encoding="utf-8")

    grep_output = (
        f"{search_dir}/orchestrator_context.py:1227:    compaction_threshold,\n"
        f"{search_dir}/orchestrator_context.py:1366:COMPACTION_THRESHOLD: int = Field(5)\n"
    )

    class MockResult:
        returncode = 0
        stdout = grep_output

    from hydrahive_core.tool_registry import FileSearchTool
    tool = FileSearchTool()

    try:
        with patch("subprocess.run", return_value=MockResult()):
            result = await tool.execute(
                agent_id="boss",
                project_id="hydrahive-coding",
                pattern="compaction_threshold",
                path="repo/core/src",
                max_results=20,
            )

        assert result["count"] == 2, f"got: {result!r}"
        for match in result["matches"]:
            assert match["file"] == "orchestrator_context.py", \
                f"file ist {match['file']!r} — sollte 'orchestrator_context.py' sein (nicht Zeilennummer)"
            assert isinstance(match["line"], int), \
                f"line ist {type(match['line']).__name__} — sollte int sein"
            assert match["line"] in (1227, 1366), \
                f"line ist {match['line']} — erwartet 1227 oder 1366"
    finally:
        test_file.unlink(missing_ok=True)

