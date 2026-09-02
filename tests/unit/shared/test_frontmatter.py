"""The shared frontmatter codec: fences are whole lines, and `render`
is `parse`'s inverse — real YAML, never `f"{key}: {value}"`."""

import pytest

from neosian._foundation.shared.frontmatter import (
    FrontmatterError,
    parse_frontmatter,
    render_frontmatter,
)


@pytest.mark.unit
class TestParse:
    def test_a_dash_run_inside_a_value_does_not_close_the_block(self) -> None:
        text = "---\nname: x\ndescription: see --- the divider\n---\nbody\n"
        frontmatter, body = parse_frontmatter(text, "x.md")
        assert frontmatter == {"name": "x", "description": "see --- the divider"}
        assert body == "body"

    def test_crlf_fences_are_accepted(self) -> None:
        text = "---\r\nname: x\r\ndescription: d\r\n---\r\nbody\r\n"
        frontmatter, body = parse_frontmatter(text, "x.md")
        assert frontmatter["name"] == "x"
        assert body == "body"

    @pytest.mark.parametrize(
        "text",
        [
            "no frontmatter",
            "---\nname: x\nbody",
            "---\n- a list\n---\nbody",
            "---\n---\n",
        ],
    )
    def test_missing_unclosed_or_non_mapping_raises(self, text: str) -> None:
        with pytest.raises(FrontmatterError):
            parse_frontmatter(text, "x.md")


@pytest.mark.unit
class TestRender:
    @pytest.mark.parametrize(
        "description",
        ["plain", "a: colon", "quotes 'and' \"more\"", "--- dashes", "ünïcödé"],
    )
    def test_round_trips_hostile_values(self, description: str) -> None:
        frontmatter = {"name": "x", "description": description}
        text = render_frontmatter(frontmatter, "body\nlines")
        assert parse_frontmatter(text, "x.md") == (frontmatter, "body\nlines")

    def test_keeps_key_order_and_the_blank_line_before_the_body(self) -> None:
        text = render_frontmatter({"name": "x", "description": "d"}, "body")
        assert text == "---\nname: x\ndescription: d\n---\n\nbody\n"
