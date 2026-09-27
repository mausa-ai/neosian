"""How a search answers (§32): the header, the atom-safe snippet, the
three renderings."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from neosian._foundation.conversation.hits import (
    _ATOM_MAX,
    SNIPPET_CHARS,
    hit_block,
    hit_header,
    hit_json,
    hit_line,
    render_hits,
    snippet,
)
from neosian._foundation.conversation.types import ConversationTurn
from neosian._foundation.llm.base import Message, Role

_WHEN = datetime(2026, 9, 27, 10, 0, tzinfo=UTC)


def _turn(text: str, *, actor: str | None = None, number: int = 3) -> ConversationTurn:
    return ConversationTurn(
        conversation_id="cc-1",
        turn=number,
        messages=(
            Message(role=Role.USER, content=text),
            Message(role=Role.ASSISTANT, content="noted"),
        ),
        created_at=_WHEN,
        actor=actor,
    )


@pytest.mark.unit
class TestTheHeader:
    def test_names_the_turn_the_stamp_and_the_actor(self) -> None:
        assert hit_header(_turn("x", actor="claude-code:s")) == (
            "[cc-1 #3] 2026-09-27T10:00:00Z claude-code:s"
        )

    def test_a_missing_actor_is_a_dash(self) -> None:
        assert hit_header(_turn("x")).endswith(" -")


@pytest.mark.unit
class TestTheSnippet:
    def test_a_short_line_is_whole(self) -> None:
        assert snippet("hello wide world", ["world"]) == "hello wide world"

    def test_the_first_line_holding_a_term_wins(self) -> None:
        assert snippet("alpha\nbeta  gamma\ngamma delta", ["gamma"]) == "beta gamma"

    def test_the_line_holding_more_of_the_terms_wins(self) -> None:
        text = "the cache is tuned\nthe cache TTL is 86400\nttl noted"
        assert snippet(text, ["cache", "ttl"]) == "the cache TTL is 86400"

    def test_no_match_is_the_first_line(self) -> None:
        assert snippet("first line\nsecond", ["zzz"]) == "first line"

    def test_the_match_is_case_insensitive_and_a_quarter_in(self) -> None:
        text = " ".join(f"w{i:03d}" for i in range(80))  # 399 chars
        text = text.replace("w040", "NEEDLE")
        clip = snippet(text, ["needle"])
        assert clip.startswith("… ") and clip.endswith(" …")
        assert "NEEDLE" in clip and len(clip) <= SNIPPET_CHARS + 2 * _ATOM_MAX
        assert clip.index("NEEDLE") < len(clip) // 2

    def test_a_match_at_the_edges_cuts_one_side_only(self) -> None:
        text = " ".join(f"w{i:03d}" for i in range(80))
        head = snippet("needle " + text, ["needle"])
        tail = snippet(text + " needle", ["needle"])
        assert head.startswith("needle") and head.endswith(" …")
        assert tail.startswith("… ") and tail.endswith("needle")

    def test_an_atom_at_the_edge_is_kept_whole(self) -> None:
        url = "https://example.com/a/b/c/d/e/f/g/h"
        text = "needle " + "x " * 70 + url + " " + "y " * 60
        clip = snippet(text, ["needle"])
        assert url in clip

    def test_a_giant_atom_at_the_edge_is_dropped_whole(self) -> None:
        blob = "/" + "/".join(f"seg{i}" for i in range(40))
        text = "needle " + "x " * 70 + blob + " " + "y " * 60
        clip = snippet(text, ["needle"])
        assert blob not in clip and "seg0" not in clip and clip.endswith(" …")


@pytest.mark.unit
class TestTheRenderings:
    def test_block_line_and_json(self) -> None:
        turn = _turn("the cache TTL is 86400", actor="conv:a")
        assert hit_block(turn, ["ttl"]) == (
            "[cc-1 #3] 2026-09-27T10:00:00Z conv:a\n  the cache TTL is 86400"
        )
        assert hit_line(turn, ["ttl"]) == (
            "[cc-1 #3] 2026-09-27T10:00:00Z conv:a  the cache TTL is 86400"
        )
        assert hit_json(turn, ["ttl"]) == {
            "conversation_id": "cc-1",
            "turn": 3,
            "created_at": "2026-09-27T10:00:00Z",
            "actor": "conv:a",
            "snippet": "the cache TTL is 86400",
        }

    def test_hits_end_with_the_footer(self) -> None:
        text = render_hits([_turn("a"), _turn("b", number=4)], ["a"], footer="[end]")
        assert text.splitlines()[-1] == "[end]"
        assert text.count("[cc-1 #") == 2
