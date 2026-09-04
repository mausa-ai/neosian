"""The log-projection pure functions (DESIGN §9.6): selection, log lines,
and the rendered view. No I/O, no model — deterministic by construction."""

from datetime import UTC, datetime

import pytest

from neosian import ToolResult
from neosian._foundation.conversation.links import LinkRegistry
from neosian._foundation.conversation.projection import (
    agent_prose,
    entry_line,
    log_line,
    needs_distillation,
    render_turn,
    render_view,
    select,
)
from neosian._foundation.conversation.types import (
    ConversationProjection,
    ConversationTurn,
)
from neosian._foundation.llm.base import Message, Role, ToolCall
from neosian._foundation.shared.types import ToolCallId, ToolName

_LINKS = LinkRegistry()


def _turn(number: int, *messages: Message) -> ConversationTurn:
    return ConversationTurn(
        conversation_id="t",
        turn=number,
        messages=tuple(messages),
        created_at=datetime(2026, 8, 19, tzinfo=UTC),
    )


def _exchange(number: int, user: str, agent: str) -> ConversationTurn:
    return _turn(
        number,
        Message(role=Role.USER, content=user),
        Message(role=Role.ASSISTANT, content=agent),
    )


def _tool_round(number: int) -> ConversationTurn:
    """USER → ASSISTANT(tool_calls) → TOOL → ASSISTANT, provider order."""
    call = ToolCall(
        id=ToolCallId("c1"), name=ToolName("echo"), arguments={"text": "hi"}
    )
    return _turn(
        number,
        Message(role=Role.USER, content="run the tool"),
        Message(role=Role.ASSISTANT, content=None, tool_calls=[call]),
        Message(
            role=Role.TOOL,
            content=ToolResult.ok("echo: hi").to_json(),
            tool_call_id=ToolCallId("c1"),
        ),
        Message(role=Role.ASSISTANT, content="done"),
    )


@pytest.mark.unit
class TestLogLine:
    def test_full_word_role_labels_in_provider_order(self) -> None:
        line = log_line(_tool_round(3), digest_chars=200, user_chars=800, links=_LINKS)
        user, tool, agent = line.split(" | ")
        assert user == "USER: run the tool"
        assert tool.startswith('TOOL echo({"text":"hi"}) → ')
        assert agent == "AGENT: done"

    def test_tool_segment_carries_args_digest_and_result(self) -> None:
        line = log_line(_tool_round(1), digest_chars=200, user_chars=800, links=_LINKS)
        assert 'TOOL echo({"text":"hi"}) → echo: hi' in line

    def test_failed_tool_result_is_labeled(self) -> None:
        call = ToolCall(id=ToolCallId("c1"), name=ToolName("echo"), arguments={})
        turn = _turn(
            1,
            Message(role=Role.USER, content="go"),
            Message(role=Role.ASSISTANT, content=None, tool_calls=[call]),
            Message(
                role=Role.TOOL,
                content=ToolResult.fail("boom").to_json(),
                tool_call_id=ToolCallId("c1"),
            ),
        )
        line = log_line(turn, digest_chars=200, user_chars=800, links=_LINKS)
        assert "→ error: boom" in line

    def test_user_text_survives_verbatim_under_the_cap(self) -> None:
        text = "keep every word of this constraint"
        line = log_line(
            _exchange(2, text, "ok"), digest_chars=10, user_chars=100, links=_LINKS
        )
        assert f"USER: {text}" in line

    def test_long_user_text_head_clips_with_recall_pointer(self) -> None:
        line = log_line(
            _exchange(7, "x" * 500, "ok"), digest_chars=50, user_chars=200, links=_LINKS
        )
        assert "[recall_turn(7)]" in line
        assert "x" * 500 not in line

    def test_agent_prose_clips_at_digest_chars(self) -> None:
        line = log_line(
            _exchange(1, "hi", "y" * 500),
            digest_chars=100,
            user_chars=400,
            links=_LINKS,
        )
        assert "y" * 500 not in line
        assert "AGENT: " + "y" * 100 + " …" in line

    def test_agent_override_splices_once_and_keeps_other_segments(self) -> None:
        turn = _turn(
            4,
            Message(role=Role.USER, content="go"),
            Message(role=Role.ASSISTANT, content="first prose"),
            Message(role=Role.ASSISTANT, content="second prose"),
        )
        line = log_line(
            turn,
            digest_chars=200,
            user_chars=800,
            links=_LINKS,
            agent_override="the digest",
        )
        assert line == "USER: go | AGENT: the digest"

    def test_newlines_flatten_to_one_line(self) -> None:
        line = log_line(
            _exchange(1, "a\nb", "c\r\nd"),
            digest_chars=200,
            user_chars=800,
            links=_LINKS,
        )
        assert "\n" not in line
        assert line == "USER: a b | AGENT: c d"


@pytest.mark.unit
class TestDistillationGate:
    def test_keys_on_assistant_prose_only(self) -> None:
        long_user = _exchange(1, "u" * 1000, "short")
        assert not needs_distillation(long_user, digest_chars=200, links=_LINKS)
        long_agent = _exchange(1, "short", "a" * 1000)
        assert needs_distillation(long_agent, digest_chars=200, links=_LINKS)

    def test_agent_prose_joins_segments_in_order(self) -> None:
        turn = _turn(
            1,
            Message(role=Role.USER, content="go"),
            Message(role=Role.ASSISTANT, content="one"),
            Message(role=Role.ASSISTANT, content="two"),
        )
        assert agent_prose(turn) == "one\ntwo"


@pytest.mark.unit
class TestSelect:
    def test_widest_span_wins_over_a_later_narrower_entry(self) -> None:
        entries = (
            ConversationProjection(turn=5, kind="epoch", text="fold", span=5),
            ConversationProjection(turn=6, kind="log", text="narrow", span=2),
        )
        winner = select(entries)
        assert winner[5] == 0  # the fold, not the later narrow entry
        assert winner[6] == 1

    def test_equal_span_ties_resolve_to_the_later_entry(self) -> None:
        entries = (
            ConversationProjection(turn=3, kind="log", text="old"),
            ConversationProjection(turn=3, kind="log", text="new"),
        )
        assert select(entries)[3] == 1

    def test_labels_carry_the_turn_ref(self) -> None:
        single = ConversationProjection(turn=7, kind="log", text="x")
        fold = ConversationProjection(turn=6, kind="epoch", text="y", span=4)
        assert entry_line(single) == "[7] x"
        assert entry_line(fold) == "[3-6] y"


@pytest.mark.unit
class TestRenderView:
    def test_no_projections_renders_the_flat_history(self) -> None:
        turns = [_exchange(1, "a", "b"), _tool_round(2)]
        view = render_view(turns, ())
        assert view == [m for t in turns for m in t.messages]

    def test_projected_turns_collapse_into_one_user_log_block(self) -> None:
        turns = [_exchange(1, "a", "b"), _exchange(2, "c", "d"), _exchange(3, "e", "f")]
        entries = (
            ConversationProjection(turn=1, kind="log", text="one"),
            ConversationProjection(turn=2, kind="log", text="two"),
        )
        view = render_view(turns, entries)
        assert len(view) == 1 + len(turns[2].messages)
        block = view[0]
        assert block.role is Role.USER
        assert isinstance(block.content, str)
        assert "[1] one\n[2] two" in block.content
        assert "recall_turn" in block.content  # the footer names the tool
        assert view[1:] == list(turns[2].messages)

    def test_non_contiguous_coverage_produces_two_blocks(self) -> None:
        turns = [_exchange(n, f"u{n}", f"a{n}") for n in (1, 2, 3)]
        entries = (
            ConversationProjection(turn=1, kind="log", text="one"),
            ConversationProjection(turn=3, kind="log", text="three"),
        )
        view = render_view(turns, entries)
        assert [m.role for m in view] == [
            Role.USER,
            Role.USER,
            Role.ASSISTANT,
            Role.USER,
        ]
        assert isinstance(view[0].content, str) and "[1] one" in view[0].content
        assert view[1:3] == list(turns[1].messages)
        assert isinstance(view[3].content, str) and "[3] three" in view[3].content

    def test_a_projected_tool_round_leaves_no_orphan_tool_message(self) -> None:
        turns = [_tool_round(1), _exchange(2, "next", "ok")]
        entries = (ConversationProjection(turn=1, kind="log", text="tooling"),)
        view = render_view(turns, entries)
        assert all(m.role is not Role.TOOL for m in view)
        assert all(not m.tool_calls for m in view)

    def test_a_fold_supersedes_its_per_turn_entries(self) -> None:
        turns = [_exchange(n, f"u{n}", f"a{n}") for n in (1, 2, 3)]
        entries = (
            ConversationProjection(turn=1, kind="log", text="one"),
            ConversationProjection(turn=2, kind="log", text="two"),
            ConversationProjection(turn=2, kind="epoch", text="the fold", span=2),
        )
        view = render_view(turns, entries)
        block = view[0]
        assert isinstance(block.content, str)
        assert "[1-2] the fold" in block.content
        assert "[1] one" not in block.content


@pytest.mark.unit
class TestRenderTurn:
    def test_verbatim_role_labels_and_tool_names(self) -> None:
        text = render_turn(_tool_round(7))
        assert text.startswith("Turn 7 (verbatim):")
        assert "USER: run the tool" in text
        assert 'AGENT calls echo({"text":"hi"})' in text
        assert "TOOL echo → " in text
        assert '"echo: hi"' in text or "echo: hi" in text
        assert "AGENT: done" in text

    def test_no_clipping(self) -> None:
        text = render_turn(_exchange(1, "u" * 5000, "a" * 5000))
        assert "u" * 5000 in text
        assert "a" * 5000 in text
