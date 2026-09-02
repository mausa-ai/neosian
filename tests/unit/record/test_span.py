"""The span: payloads reduced, messages built, the sessions document (§20.9)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from neosian._foundation.llm.base import Role
from neosian._foundation.record.span import (
    TOOL_OUTPUT_CHARS,
    last_prompt,
    messages_of,
    parse_payload,
    reduce_payload,
    sessions_document,
    sessions_path,
)
from tests.unit.record.payloads import SESSION, prompt, stop, tool


class TestParsePayload:
    @pytest.mark.parametrize(
        ("text", "reason"),
        [
            ("{not json", "not JSON"),
            ("[1, 2]", "not a JSON object"),
            ('{"hook_event_name": "Stop"}', "no session_id"),
            ('{"session_id": ""}', "no session_id"),
        ],
    )
    def test_a_bad_payload_names_what_is_wrong(self, text: str, reason: str) -> None:
        with pytest.raises(ValueError, match=reason):
            parse_payload(text)

    def test_a_payload_round_trips(self) -> None:
        assert parse_payload(json.dumps(stop()))["session_id"] == SESSION


class TestReducePayload:
    def test_the_prompt_is_read_from_either_key(self) -> None:
        assert reduce_payload(prompt("hi")) == (
            "spooled",
            {"kind": "prompt", "text": "hi"},
        )
        newer = {**prompt(), "prompt": None, "user_prompt": "newer"}
        assert reduce_payload(newer)[1] == {"kind": "prompt", "text": "newer"}

    def test_a_tool_round_keeps_id_name_input_and_the_text(self) -> None:
        disposition, record = reduce_payload(tool())
        assert disposition == "spooled" and record is not None
        assert record["id"] == "toolu_01ABC" and record["name"] == "Read"
        assert record["input"] == {"file_path": "/home/u/proj/app.py"}
        assert record["response"] == "def login(): ..."

    def test_a_non_text_response_is_json_and_a_string_is_itself(self) -> None:
        _, record = reduce_payload(tool(response={"stdout": "ok", "exit": 0}))
        assert record is not None and json.loads(record["response"]) == {
            "exit": 0,
            "stdout": "ok",
        }
        _, record = reduce_payload(tool(response="plain"))
        assert record is not None and record["response"] == "plain"

    def test_a_long_response_keeps_its_head(self) -> None:
        _, record = reduce_payload(tool(response="x" * (TOOL_OUTPUT_CHARS + 10)))
        assert record is not None
        assert record["response"].startswith("x" * TOOL_OUTPUT_CHARS)
        assert record["response"].endswith("[truncated 10 chars]")

    def test_a_subagents_round_is_skipped(self) -> None:
        assert reduce_payload(tool(agent_id="agent-7")) == ("skipped", None)

    def test_an_untracked_event_is_ignored(self) -> None:
        payload = {**stop(), "hook_event_name": "SessionStart", "source": "startup"}
        assert reduce_payload(payload) == ("ignored", None)

    def test_the_stop_carries_the_final_text(self) -> None:
        assert reduce_payload(stop("bye")) == (
            "spooled",
            {"kind": "stop", "text": "bye"},
        )


class TestMessages:
    def test_the_span_is_provider_order(self) -> None:
        records = [
            reduce_payload(prompt("q"))[1],
            reduce_payload(tool())[1],
            reduce_payload(tool(name="Bash", tool_use_id=None, response="ran"))[1],
            reduce_payload(stop("a"))[1],
        ]
        messages = messages_of([r for r in records if r is not None])
        assert [m.role for m in messages] == [
            Role.USER,
            Role.ASSISTANT,
            Role.TOOL,
            Role.ASSISTANT,
            Role.TOOL,
            Role.ASSISTANT,
        ]
        assert messages[1].tool_calls[0].id == "toolu_01ABC"
        assert messages[2].tool_call_id == "toolu_01ABC"
        assert messages[3].tool_calls[0].id == "round-2"  # no tool_use_id: numbered
        assert messages[4].content == "ran" and messages[5].content == "a"

    def test_an_empty_stop_adds_no_message(self) -> None:
        assert messages_of([{"kind": "stop", "text": ""}]) == []
        assert messages_of([]) == []

    def test_the_last_prompt_wins(self) -> None:
        records = [{"kind": "prompt", "text": "one"}, {"kind": "prompt", "text": "two"}]
        assert last_prompt(records) == "two"
        assert last_prompt([{"kind": "stop", "text": "x"}]) is None


class TestSessionsDocument:
    def test_the_document_is_small_and_first_line_only(self) -> None:
        started = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
        text = sessions_document(
            agent="claude-code",
            session_id=SESSION,
            started=started,
            last_prompt="first line\nsecond line",
            turns=3,
        )
        assert text.startswith(f"# claude-code session {SESSION}\n")
        assert "- started: 2026-09-02T10:00:00Z\n" in text
        assert "- last prompt: first line\n" in text and "second" not in text
        assert text.endswith("- turns: 3\n")

    def test_no_prompt_is_a_dash(self) -> None:
        text = sessions_document(
            agent="a",
            session_id="s",
            started=datetime.now(UTC),
            last_prompt=None,
            turns=1,
        )
        assert "- last prompt: -\n" in text

    def test_the_path_is_under_sessions(self) -> None:
        assert sessions_path(SESSION) == f"sessions/{SESSION}"
