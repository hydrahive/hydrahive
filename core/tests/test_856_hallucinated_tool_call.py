"""
test_856_hallucinated_tool_call.py — Detector fuer in Text ausgegebene
Tool-Call-Muster (#856).

MiniMax gibt gelegentlich "[TOOL_CALL]\n{tool => "shell_exec"...}" als
Plaintext aus statt einen echten tool_use-Block. Der Orchestrator nutzt
is_hallucinated_tool_call() um das zu detektieren und einen Retry-Prompt
einzuhaengen.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.orchestrator_tools import is_hallucinated_tool_call


def test_detects_tool_call_marker():
    """Standard-Halluzination mit [TOOL_CALL]-Markern."""
    content = """Ich rufe jetzt das Tool auf:

[TOOL_CALL]
{tool => "shell_exec", args => {
  --command "ls /tmp"
}}
[/TOOL_CALL]

Warte auf das Ergebnis."""
    assert is_hallucinated_tool_call(content) is True


def test_detects_closed_marker_alone():
    """Nur [/TOOL_CALL] reicht schon — Halluzination-Signal."""
    content = "Something something. [/TOOL_CALL] Trailing text."
    assert is_hallucinated_tool_call(content) is True


def test_detects_tool_arrow_at_line_start():
    """Boss gibt ohne [TOOL_CALL]-Marker nur `{tool => ...}` aus."""
    content = """Let me execute this:

{tool => "file_read", args => {path => "/foo"}}

Done."""
    assert is_hallucinated_tool_call(content) is True


def test_detects_json_style_tool_args():
    """Boss kann auch den JSON-Style produzieren: {"tool":"foo","args":..."""
    content = """Ok ich mache:

{"tool": "shell_exec", "args": {"command": "pwd"}}

Fertig."""
    assert is_hallucinated_tool_call(content) is True


def test_ignores_prose_mentioning_tools():
    """Prosa die nur von "tool" spricht darf nicht flaggen."""
    content = (
        "Dieses Tool ist file_read. Es liest Dateien. "
        "Ich koennte es aufrufen wenn noetig."
    )
    assert is_hallucinated_tool_call(content) is False


def test_ignores_empty_string():
    assert is_hallucinated_tool_call("") is False


def test_ignores_none():
    # defensive — None sollte nicht crashen sondern False geben
    assert is_hallucinated_tool_call(None) is False  # type: ignore[arg-type]


def test_ignores_short_content():
    """Unter Mindestlaenge (15 chars) kein Flag — verhindert False-Positives
    auf sehr kurzen Tool-Namen-Erwaehnungen."""
    assert is_hallucinated_tool_call("tool =>") is False


def test_ignores_markdown_code_block():
    """Markdown-Codeblock der Tool-Call Syntax DOKUMENTIERT
    (z.B. in einer Erklaerung) flaggt, was OK ist — der Retry-Prompt
    sagt Boss nur er soll formal wiederholen; wenn es Dokumentation war,
    wird der Retry keine Halluzination produzieren und ein anderer
    Mechanismus greift. Dieses Edge-Case ist akzeptierter Noise."""
    content = """Beispiel fuer einen Tool-Call-Output:

```
[TOOL_CALL]
{tool => "shell_exec", args => {...}}
[/TOOL_CALL]
```

So sieht es aus."""
    # Dies flaggt bewusst — Retry-Prompt ist harmlos
    assert is_hallucinated_tool_call(content) is True


def test_ignores_normal_assistant_response():
    """Echter Assistant-Text ohne Halluzination."""
    content = (
        "Ich habe die Datei gelesen. Inhalt ist README mit Setup-Anweisungen. "
        "Was soll ich als naechstes tun? Soll ich einen Patch vorbereiten oder "
        "erst einen Plan entwerfen?"
    )
    assert is_hallucinated_tool_call(content) is False


# #862: minimax:tool_call XML-Varianten
def test_detects_minimax_xml_closing_tag():
    assert is_hallucinated_tool_call("Der Patch ist fertig.\n</minimax:tool_call>") is True


def test_detects_minimax_xml_opening_tag():
    assert is_hallucinated_tool_call('<minimax:tool_call name="file_write">') is True


def test_detects_generic_ns_tool_call_tag():
    assert is_hallucinated_tool_call("erledigt\n</foo:tool_call>") is True
