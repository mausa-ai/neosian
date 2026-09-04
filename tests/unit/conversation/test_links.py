"""Link handles (DESIGN §23): the atom classifier, the registry as a pure
function of history, contraction, expansion, and atom-safe clipping."""

from datetime import UTC, datetime

import pytest

from neosian import ToolResult
from neosian._foundation.conversation.links import (
    LINK_CHARS,
    LinkRegistry,
    boundary,
)
from neosian._foundation.conversation.projection import (
    log_line,
    one_line,
    render_view,
)
from neosian._foundation.conversation.types import (
    ConversationProjection,
    ConversationTurn,
)
from neosian._foundation.llm.base import Message, Role, ToolCall
from neosian._foundation.shared.context_policy import ContextPolicy
from neosian._foundation.shared.types import ToolCallId, ToolName

_URL = "https://docs.example.test/specs/0f8fad5b-d9cb-469f-a165-70867728950e/v2"
_PATH = "/Users/ada/Documents/projects/neosian/_foundation/conversation/links.py"
_SHA = "0cfb519a4b3c2d1e0f9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e"


def _turn(number: int, *messages: Message) -> ConversationTurn:
    return ConversationTurn(
        conversation_id="t",
        turn=number,
        messages=tuple(messages),
        created_at=datetime(2026, 9, 4, tzinfo=UTC),
    )


def _exchange(number: int, user: str, agent: str = "ok") -> ConversationTurn:
    return _turn(
        number,
        Message(role=Role.USER, content=user),
        Message(role=Role.ASSISTANT, content=agent),
    )


def _atoms(text: str) -> list[str]:
    """Every atom of `text` by probing each interior position."""
    found: list[tuple[int, int]] = []
    for at in range(1, len(text)):
        span = boundary(text, at)
        if span != (at, at) and span not in found:
            found.append(span)
    return [text[start:end] for start, end in found]


@pytest.mark.unit
class TestAtoms:
    @pytest.mark.parametrize(
        "token",
        [
            _URL,
            _PATH,
            _SHA,
            "~/Documents/projects/neosian",
            "sess_01HXYZ7Q2K9M4N8P3R5T6V7W8X",
            "src/parse.py",
            "and/or",
            "v2.0",
            "widget-design-2026-09-04.tar.gz",
            "https://en.wikipedia.org/wiki/Foo_(bar)",
        ],
    )
    def test_a_token_is_one_atom(self, token: str) -> None:
        assert _atoms(f"see {token} here") == [token]

    @pytest.mark.parametrize("punctuation", [".", ",", ":", ")", "?!"])
    def test_trailing_punctuation_stays_outside(self, punctuation: str) -> None:
        assert _atoms(f"open {_URL}{punctuation} now") == [_URL]

    def test_delimiters_bound_a_token(self) -> None:
        text = f'[spec]({_URL}) and {{"path":"{_PATH}"}} and `{_SHA}`'
        assert _atoms(text) == [_URL, _PATH, _SHA]

    def test_plain_words_are_not_atoms(self) -> None:
        assert _atoms("a twentyfivelettersinaword sentence") == []

    def test_a_handle_is_an_atom(self) -> None:
        assert _atoms("see [link 12] and [link conv-a:3]") == [
            "[link 12]",
            "[link conv-a:3]",
        ]


@pytest.mark.unit
class TestRegistry:
    def test_first_appearance_across_roles_and_arguments(self) -> None:
        call = ToolCall(
            id=ToolCallId("c1"),
            name=ToolName("fetch"),
            arguments={"url": _URL, "nested": {"paths": [_PATH]}},
        )
        turns = [
            _exchange(1, f"read {_SHA}"),
            _turn(
                2,
                Message(role=Role.USER, content="go"),
                Message(
                    role=Role.ASSISTANT,
                    content=None,
                    reasoning=f"maybe {_PATH}",
                    tool_calls=[call],
                ),
                Message(
                    role=Role.TOOL,
                    content=ToolResult.ok(f"body at {_SHA}").to_json(),
                    tool_call_id=ToolCallId("c1"),
                ),
            ),
        ]
        registry = LinkRegistry.of(turns)
        assert len(registry) == 3
        assert registry.expand("[link 1] [link 2] [link 3]") == f"{_SHA} {_URL} {_PATH}"

    def test_reasoning_is_skipped(self) -> None:
        turn = _turn(
            1,
            Message(role=Role.USER, content="go"),
            Message(role=Role.ASSISTANT, content="ok", reasoning=_URL),
        )
        assert len(LinkRegistry.of([turn])) == 0

    def test_short_tokens_never_register(self) -> None:
        short = "https://a.b/" + "c" * (LINK_CHARS - 13)
        assert len(short) == LINK_CHARS - 1
        assert len(LinkRegistry.of([_exchange(1, short)])) == 0
        assert len(LinkRegistry.of([_exchange(1, short + "c")])) == 1

    def test_numbering_is_prefix_stable(self) -> None:
        turns = [_exchange(n, f"{_URL}/{n} then {_PATH}") for n in range(1, 5)]
        whole = LinkRegistry.of(turns)
        for k in range(1, 5):
            prefix = LinkRegistry.of(turns[:k])
            for number in range(1, len(prefix) + 1):
                handle = f"[link {number}]"
                assert prefix.expand(handle) == whole.expand(handle)

    def test_contract_swaps_whole_atoms_only(self) -> None:
        registry = LinkRegistry([_URL, _URL + "/longer"])
        text = f"{_URL} vs {_URL}/longer and {_URL}-ish"
        assert registry.contract(text) == f"[link 1] vs [link 2] and {_URL}-ish"

    def test_contract_leaves_handles_and_unknown_tokens(self) -> None:
        registry = LinkRegistry([_URL])
        text = f"[link 9] {_PATH}"
        assert registry.contract(text) == text

    def test_expand_walks_nested_values_and_keeps_unknown_numbers(self) -> None:
        registry = LinkRegistry([_URL])
        arguments = {"url": "[link 1]", "more": ["[link 2]", {"p": "x [link 1]"}]}
        assert registry.expand(arguments) == {
            "url": _URL,
            "more": ["[link 2]", {"p": f"x {_URL}"}],
        }

    def test_qualified_and_bare_handles_do_not_cross_resolve(self) -> None:
        own = LinkRegistry([_URL])
        other = LinkRegistry([_PATH], source="conv-a")
        text = "[link 1] [link conv-a:1]"
        assert other.handle(1) == "[link conv-a:1]"
        assert own.expand(text) == f"{_URL} [link conv-a:1]"
        assert other.expand(text) == f"[link 1] {_PATH}"
        assert other.contract(_PATH) == "[link conv-a:1]"


@pytest.mark.unit
class TestAtomSafeClips:
    def test_one_line_never_ends_inside_a_token(self) -> None:
        clipped = one_line(f"open {_URL} now", 20)
        assert clipped == "open …"

    def test_one_line_contracts_before_clipping(self) -> None:
        clipped = one_line(f"open {_URL} now", 20, links=LinkRegistry([_URL]))
        assert clipped == "open [link 1] now"

    def test_a_leading_oversize_token_leaves_only_the_marker(self) -> None:
        assert one_line(_URL, 10, marker=" … [recall_turn(3)]") == "… [recall_turn(3)]"

    def test_args_digest_never_ends_inside_a_url(self) -> None:
        call = ToolCall(
            id=ToolCallId("c1"), name=ToolName("fetch"), arguments={"url": _URL * 2}
        )
        turn = _turn(
            1,
            Message(role=Role.USER, content="go"),
            Message(role=Role.ASSISTANT, content=None, tool_calls=[call]),
        )
        line = log_line(turn, digest_chars=200, user_chars=800, links=LinkRegistry())
        assert 'TOOL fetch({"url":" …) → ?' in line

    def test_result_head_and_tail_never_cut_a_token(self) -> None:
        # The first sha straddles the head cut (100), the second the tail
        # cut (40 from the end): both are dropped whole, never split.
        other = _SHA[::-1]
        body = "head " * 18 + _SHA + " mid " + other + " tail"
        call = ToolCall(id=ToolCallId("c1"), name=ToolName("read"), arguments={})
        turn = _turn(
            1,
            Message(role=Role.USER, content="go"),
            Message(role=Role.ASSISTANT, content=None, tool_calls=[call]),
            Message(
                role=Role.TOOL,
                content=ToolResult.ok(body).to_json(),
                tool_call_id=ToolCallId("c1"),
            ),
        )
        line = log_line(turn, digest_chars=200, user_chars=800, links=LinkRegistry())
        assert line.endswith("→ head " + "head " * 17 + "… tail")
        line = log_line(
            turn, digest_chars=200, user_chars=800, links=LinkRegistry([_SHA, other])
        )
        assert line.endswith("[link 1] mid [link 2] tail")


@pytest.mark.unit
class TestMeasured:
    def test_handles_shrink_the_compacted_view(self) -> None:
        """The keyless half of the measurement: the same history projected
        with and without handles, estimated by the window's own policy."""
        turns = [
            _exchange(n, f"spec {n} lives at {_URL}/{n}", f"opened {_URL}/{n}: fine")
            for n in range(1, 9)
        ]
        registry = LinkRegistry.of(turns)
        estimated: dict[str, int] = {}
        for label, links in (("whole", LinkRegistry()), ("handles", registry)):
            entries = [
                ConversationProjection(
                    turn=turn.turn,
                    kind="log",
                    text=log_line(turn, digest_chars=200, user_chars=800, links=links),
                )
                for turn in turns
            ]
            estimated[label] = ContextPolicy().estimate_tokens(
                render_view(turns, entries)
            )
        saved = 100 * (estimated["whole"] - estimated["handles"]) // estimated["whole"]
        print(
            f"link handles: {estimated['whole']} → {estimated['handles']} tokens ({saved}% saved)"
        )
        assert estimated["handles"] < estimated["whole"]
