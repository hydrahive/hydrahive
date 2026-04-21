"""#792 — XML-Invoke-Parser für MiniMax-Tool-Call-Emissionen."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.minimax_invoke_parser import (
    SyntheticToolUse,
    parse_invoke_markup,
    text_without_invokes,
)


class TestParseInvokeMarkup:
    def test_single_invoke_one_param(self):
        text = '<invoke name="image_generate"><parameter name="prompt">A cat</parameter></invoke>'
        blocks = parse_invoke_markup(text)
        assert len(blocks) == 1
        assert blocks[0].name == "image_generate"
        assert blocks[0].input == {"prompt": "A cat"}

    def test_single_invoke_two_params(self):
        text = (
            '<invoke name="image_generate">'
            '<parameter name="prompt">A cat</parameter>'
            '<parameter name="aspect_ratio">1:1</parameter>'
            '</invoke>'
        )
        blocks = parse_invoke_markup(text)
        assert len(blocks) == 1
        assert blocks[0].input == {"prompt": "A cat", "aspect_ratio": "1:1"}

    def test_multiple_invokes(self):
        text = (
            '<invoke name="image_generate"><parameter name="prompt">A cat</parameter></invoke>'
            '<invoke name="music_generate"><parameter name="prompt">Jazz</parameter></invoke>'
        )
        blocks = parse_invoke_markup(text)
        assert len(blocks) == 2
        assert blocks[0].name == "image_generate"
        assert blocks[1].name == "music_generate"

    def test_param_with_newlines(self):
        text = (
            '<invoke name="image_generate">'
            '<parameter name="prompt">A very long\nmulti-line\nprompt</parameter>'
            '</invoke>'
        )
        blocks = parse_invoke_markup(text)
        assert blocks[0].input["prompt"] == "A very long\nmulti-line\nprompt"

    def test_param_with_whitespace_and_newlines_around_tags(self):
        """Realistisches MiniMax-Markup mit Einrückung."""
        text = (
            '<invoke name="image_generate">\n'
            '  <parameter name="prompt">A hamster smoking a pipe</parameter>\n'
            '  <parameter name="aspect_ratio">1:1</parameter>\n'
            '</invoke>'
        )
        blocks = parse_invoke_markup(text)
        assert len(blocks) == 1
        assert blocks[0].input == {
            "prompt": "A hamster smoking a pipe",
            "aspect_ratio": "1:1",
        }

    def test_empty_and_none(self):
        assert parse_invoke_markup("") == []
        assert parse_invoke_markup(None) == []

    def test_no_invoke_in_text(self):
        assert parse_invoke_markup("Das ist normaler Text ohne Tools.") == []

    def test_malformed_open_tag(self):
        assert parse_invoke_markup("<invoke name=") == []

    def test_malformed_no_close_tag(self):
        assert parse_invoke_markup('<invoke name="foo"><parameter name="x">y</parameter>') == []

    def test_synthetic_tool_use_fields(self):
        text = '<invoke name="music_generate"><parameter name="prompt">Jazz</parameter></invoke>'
        block = parse_invoke_markup(text)[0]
        assert isinstance(block, SyntheticToolUse)
        assert block.type == "tool_use"
        assert block.id.startswith("mm_xml_")
        assert block.name == "music_generate"
        assert block.input == {"prompt": "Jazz"}

    def test_case_insensitive(self):
        text = '<INVOKE NAME="img"><PARAMETER NAME="p">cat</PARAMETER></INVOKE>'
        blocks = parse_invoke_markup(text)
        assert len(blocks) == 1
        assert blocks[0].name == "img"
        assert blocks[0].input == {"p": "cat"}

    def test_prose_before_and_after(self):
        text = (
            "Hier ist das Bild was du wolltest:\n"
            '<invoke name="image_generate"><parameter name="prompt">A cat</parameter></invoke>\n'
            "Viel Spaß damit!"
        )
        blocks = parse_invoke_markup(text)
        assert len(blocks) == 1


class TestTextWithoutInvokes:
    def test_removes_single_block(self):
        text = 'Hello <invoke name="img"><parameter name="p">cat</parameter></invoke> World'
        assert "invoke" not in text_without_invokes(text)
        assert "Hello" in text_without_invokes(text)
        assert "World" in text_without_invokes(text)

    def test_removes_multiple_blocks(self):
        text = (
            '<invoke name="a"><parameter name="x">1</parameter></invoke>'
            " middle "
            '<invoke name="b"><parameter name="y">2</parameter></invoke>'
        )
        result = text_without_invokes(text)
        assert "invoke" not in result
        assert "middle" in result

    def test_plain_text_unchanged(self):
        text = "Just plain text, no markup."
        assert text_without_invokes(text) == text

    def test_idempotent(self):
        text = 'Hello <invoke name="x"><parameter name="p">v</parameter></invoke>'
        once = text_without_invokes(text)
        twice = text_without_invokes(once)
        assert once == twice

    def test_none_and_empty(self):
        assert text_without_invokes(None) == ""
        assert text_without_invokes("") == ""


class TestRoundtripWithStreamLogic:
    """Dokumentiert das Kontrakt zwischen Parser und orchestrator_stream."""

    def test_synthetic_blocks_are_duck_typed(self):
        """Stream-Loop greift auf .name, .input, .id, .type zu."""
        text = '<invoke name="music_generate"><parameter name="prompt">Jazz</parameter></invoke>'
        block = parse_invoke_markup(text)[0]
        assert hasattr(block, "name")
        assert hasattr(block, "input")
        assert hasattr(block, "id")
        assert hasattr(block, "type")

    def test_ids_are_unique_per_block(self):
        text = (
            '<invoke name="a"><parameter name="x">1</parameter></invoke>'
            '<invoke name="b"><parameter name="y">2</parameter></invoke>'
        )
        blocks = parse_invoke_markup(text)
        assert blocks[0].id != blocks[1].id
