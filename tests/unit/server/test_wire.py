"""The §18 wire codec: value types round-trip losslessly, timestamps stay
tz-aware, and the error envelope decodes back to the exact typed
exception — degraded to the base `NeosianError` on an unknown code or
malformed details, never swallowed."""

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from neosian._foundation.conversation.types import (
    ConversationProjection,
    ConversationTurn,
)
from neosian._foundation.llm.base import Message, Role, ToolCall
from neosian._foundation.memory.types import (
    MemoryDocument,
    MemoryEntry,
    MemoryVersion,
)
from neosian._foundation.server.wire import (
    decode_document,
    decode_entry,
    decode_error,
    decode_projection,
    decode_timestamp,
    decode_turn,
    decode_version,
    encode_document,
    encode_entry,
    encode_error,
    encode_projection,
    encode_timestamp,
    encode_turn,
    encode_version,
)
from neosian._foundation.shared.exceptions import (
    ConversationFormatUnsupportedError,
    ConversationIdInvalidError,
    MemoryConflictError,
    MemoryDocumentNotFoundError,
    MemoryFormatUnsupportedError,
    MemoryPathInvalidError,
    MemoryScopeInvalidError,
    NeosianError,
)
from neosian._foundation.shared.types import ToolCallId, ToolName

_TS = datetime(2026, 8, 23, 12, 30, 0, tzinfo=UTC)


def _json_safe(payload: object) -> object:
    """The wire is JSON: every encoded value must survive dumps/loads."""
    return json.loads(json.dumps(payload))


class TestValueTypes:
    def test_document_round_trips_with_extra(self) -> None:
        document = MemoryDocument(
            scope="user:a",
            path="notes/x",
            content="body",
            version=3,
            created_at=_TS,
            updated_at=_TS,
            actor="cli:me",
            redacted=True,
            extra={"custom": "kept", "count": 3, "flag": True},
        )
        decoded = decode_document(_json_safe(encode_document(document)))  # type: ignore[arg-type]
        assert decoded == MemoryDocument(
            scope="user:a",
            path="notes/x",
            content="body",
            version=3,
            created_at=_TS,
            updated_at=_TS,
            actor="cli:me",
            redacted=True,
            extra={"custom": "kept", "count": 3, "flag": True},
        )

    def test_entry_and_version_round_trip(self) -> None:
        entry = MemoryEntry(
            path="a", version=1, created_at=_TS, updated_at=_TS, redacted=False
        )
        assert decode_entry(_json_safe(encode_entry(entry))) == entry  # type: ignore[arg-type]
        row = MemoryVersion(
            path="a",
            version=2,
            action="deleted",
            content="",
            actor=None,
            created_at=_TS,
            redacted=False,
        )
        assert decode_version(_json_safe(encode_version(row))) == row  # type: ignore[arg-type]

    def test_turn_round_trips_verbatim_messages(self) -> None:
        turn = ConversationTurn(
            conversation_id="thread-1",
            turn=4,
            messages=(
                Message(role=Role.USER, content="hi"),
                Message(
                    role=Role.ASSISTANT,
                    content=None,
                    reasoning="thinking",
                    tool_calls=[
                        ToolCall(
                            id=ToolCallId("c1"),
                            name=ToolName("memory"),
                            arguments={"command": "view"},
                        )
                    ],
                ),
                Message(role=Role.TOOL, content="ok", tool_call_id=ToolCallId("c1")),
            ),
            created_at=_TS,
        )
        decoded = decode_turn(_json_safe(encode_turn(turn)))  # type: ignore[arg-type]
        assert decoded == turn

    def test_projection_round_trips_and_validates(self) -> None:
        entry = ConversationProjection(turn=5, kind="digest", text="t", span=3)
        assert decode_projection(_json_safe(encode_projection(entry))) == entry  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="span"):
            decode_projection({"turn": 2, "kind": "log", "text": "", "span": 5})


class TestTimestamps:
    def test_z_suffix_round_trip(self) -> None:
        assert encode_timestamp(_TS) == "2026-08-23T12:30:00Z"
        assert decode_timestamp("2026-08-23T12:30:00Z") == _TS

    def test_offsets_normalise_to_utc_both_ways(self) -> None:
        # ECOSYSTEM §9: ISO-8601 `Z` on the wire whatever zone a clock
        # spoke, and a foreign offset lands as UTC (the postgres/rows.py rule).
        plus_two = datetime(2026, 8, 23, 14, 30, tzinfo=timezone(timedelta(hours=2)))
        assert encode_timestamp(plus_two) == "2026-08-23T12:30:00Z"
        decoded = decode_timestamp("2026-08-23T14:30:00+02:00")
        assert decoded == _TS
        assert decoded.tzinfo is UTC

    def test_naive_refused_both_ways(self) -> None:
        with pytest.raises(ValueError, match="naive"):
            encode_timestamp(datetime(2026, 1, 1))  # noqa: DTZ001 - the case
        with pytest.raises(ValueError, match="naive"):
            decode_timestamp("2026-01-01T00:00:00")


class TestErrorEnvelope:
    @pytest.mark.parametrize(
        "original",
        [
            MemoryDocumentNotFoundError("user:a", "gone"),
            MemoryScopeInvalidError("bad scope", "spaces are not allowed"),
            MemoryPathInvalidError("../x", "escapes"),
            MemoryConflictError(
                "user:a",
                "x",
                "version_mismatch",
                expected_version=2,
                actual_version=5,
            ),
            MemoryFormatUnsupportedError("user:a", "x", "format 999 is newer"),
            ConversationIdInvalidError("a/b", "one flat segment"),
            ConversationFormatUnsupportedError("t", "malformed turn-log line 1"),
        ],
    )
    def test_typed_exceptions_round_trip(self, original: NeosianError) -> None:
        decoded = decode_error(_json_safe(encode_error(original)))  # type: ignore[arg-type]
        assert type(decoded) is type(original)
        assert isinstance(decoded, NeosianError)
        assert decoded.code == original.code
        assert decoded.message == original.message
        assert decoded.details == original.details

    def test_conflict_fields_survive(self) -> None:
        decoded = decode_error(
            encode_error(
                MemoryConflictError(
                    "user:a", "x", "revert_stale", expected_version=1, actual_version=9
                )
            )
        )
        assert isinstance(decoded, MemoryConflictError)
        assert decoded.reason == "revert_stale"
        assert decoded.expected_version == 1
        assert decoded.actual_version == 9

    def test_value_error_rides_the_sentinel(self) -> None:
        decoded = decode_error(encode_error(ValueError("after must be >= 0, got -1")))
        assert type(decoded) is ValueError
        assert str(decoded) == "after must be >= 0, got -1"

    def test_unknown_code_degrades_to_the_base(self) -> None:
        decoded = decode_error(
            {"code": "memory_future_thing", "message": "m", "details": {"a": 1}}
        )
        assert type(decoded) is NeosianError
        assert decoded.message == "m"
        assert decoded.details == {"a": 1}

    def test_malformed_details_degrade_to_the_base(self) -> None:
        decoded = decode_error(
            {"code": "memory_conflict", "message": "m", "details": {}}
        )
        assert type(decoded) is NeosianError
        assert decoded.message == "m"
