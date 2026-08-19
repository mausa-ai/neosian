"""Tests for the storage envelope codec (memory/envelope.py)."""

from datetime import UTC, datetime

import pytest

from neosian._foundation.memory.envelope import Envelope, parse, render
from neosian._foundation.shared.exceptions import MemoryFormatUnsupportedError

_SCOPE = "user:test"
_PATH = "notes/api"
_T0 = datetime(2026, 8, 19, 10, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 8, 19, 10, 5, 0, tzinfo=UTC)


def _envelope(**overrides: object) -> Envelope:
    defaults: dict[str, object] = {
        "version": 3,
        "created_at": _T0,
        "updated_at": _T1,
        "actor": "conv-1",
    }
    defaults.update(overrides)
    return Envelope(**defaults)  # type: ignore[arg-type]


class TestRoundTrip:
    @pytest.mark.parametrize(
        "content",
        [
            "",
            "x",
            "x\n",
            "x\n\n",
            "\n",
            "  leading and trailing  ",
            "line one\nline two\n",
            "a\r\nb",  # CRLF inside content survives verbatim
            "---\ntitle: t\n---\nbody",  # content that IS a frontmatter doc
            "---",
            "unicode: café ✓",
            "big" * 4000,  # ~12KB — past any line-wrap threshold
        ],
    )
    def test_content_is_byte_exact(self, content: str) -> None:
        envelope = _envelope()
        parsed, restored = parse(render(envelope, content), scope=_SCOPE, path=_PATH)
        assert restored == content
        assert parsed == envelope

    def test_extra_keys_round_trip(self) -> None:
        extra = {"custom": "kept", "count": 3, "flag": True}
        parsed, _ = parse(
            render(_envelope(extra=extra), "body"), scope=_SCOPE, path=_PATH
        )
        assert dict(parsed.extra) == extra

    def test_multiline_extra_value_round_trips(self) -> None:
        # A value containing a line "---" must not break fence detection.
        extra = {"note": "a\n---\nb"}
        parsed, content = parse(
            render(_envelope(extra=extra), "body"), scope=_SCOPE, path=_PATH
        )
        assert dict(parsed.extra) == extra
        assert content == "body"

    def test_long_extra_value_is_not_line_wrapped(self) -> None:
        extra = {"url": "https://example.com/" + "a" * 300}
        parsed, _ = parse(render(_envelope(extra=extra), ""), scope=_SCOPE, path=_PATH)
        assert dict(parsed.extra) == extra

    def test_none_actor_and_false_redacted_are_omitted_from_the_wire(self) -> None:
        text = render(_envelope(actor=None), "")
        assert "actor" not in text.split("---")[1]
        assert "redacted" not in text
        parsed, _ = parse(text, scope=_SCOPE, path=_PATH)
        assert parsed.actor is None
        assert parsed.redacted is False

    def test_timestamps_are_iso_z_strings_on_the_wire(self) -> None:
        text = render(_envelope(), "")
        assert "created_at: '2026-08-19T10:00:00Z'" in text
        parsed, _ = parse(text, scope=_SCOPE, path=_PATH)
        assert parsed.created_at == _T0
        assert parsed.created_at.tzinfo is not None


class TestParseRefuses:
    @pytest.mark.parametrize(
        ("text", "reason_fragment"),
        [
            ("no fence at all", "missing envelope fence"),
            ("---\nversion: 1\nno closing", "unterminated"),
            ("---\n[not: a: mapping\n---\n", "invalid envelope YAML"),
            ("---\n- just\n- a list\n---\n", "not a mapping"),
            ("---\nversion: 1\n---\n", "neosian_format"),
            ("---\nneosian_format: true\nversion: 1\n---\n", "neosian_format"),
        ],
    )
    def test_malformed_envelopes(self, text: str, reason_fragment: str) -> None:
        with pytest.raises(MemoryFormatUnsupportedError) as exc_info:
            parse(text, scope=_SCOPE, path=_PATH)
        assert reason_fragment in exc_info.value.reason
        assert exc_info.value.code == "memory_format_unsupported"

    def test_newer_format_version_is_refused(self) -> None:
        text = render(_envelope(), "body").replace(
            "neosian_format: 1", "neosian_format: 2"
        )
        with pytest.raises(MemoryFormatUnsupportedError) as exc_info:
            parse(text, scope=_SCOPE, path=_PATH)
        assert "newer" in exc_info.value.reason

    def test_naive_timestamp_is_refused_never_coerced(self) -> None:
        text = render(_envelope(), "").replace(
            "created_at: '2026-08-19T10:00:00Z'",
            "created_at: '2026-08-19T10:00:00'",
        )
        with pytest.raises(MemoryFormatUnsupportedError) as exc_info:
            parse(text, scope=_SCOPE, path=_PATH)
        assert "naive" in exc_info.value.reason

    def test_bare_yaml_date_is_refused(self) -> None:
        # Hand edits produce these; YAML parses them to datetime.date.
        text = render(_envelope(), "").replace(
            "created_at: '2026-08-19T10:00:00Z'", "created_at: 2026-08-19"
        )
        with pytest.raises(MemoryFormatUnsupportedError):
            parse(text, scope=_SCOPE, path=_PATH)

    def test_aware_yaml_datetime_from_hand_edit_is_accepted(self) -> None:
        # YAML's native timestamp type with an offset is tz-aware — legal.
        text = render(_envelope(), "").replace(
            "created_at: '2026-08-19T10:00:00Z'",
            "created_at: 2026-08-19 10:00:00+00:00",
        )
        parsed, _ = parse(text, scope=_SCOPE, path=_PATH)
        assert parsed.created_at == _T0

    def test_version_zero_is_refused(self) -> None:
        text = render(_envelope(), "").replace("version: 3", "version: 0")
        with pytest.raises(MemoryFormatUnsupportedError):
            parse(text, scope=_SCOPE, path=_PATH)

    def test_crlf_fence_lines_are_accepted(self) -> None:
        # A hand edit on Windows may re-save fences with CRLF endings.
        text = render(_envelope(), "body").replace("---\n", "---\r\n", 1)
        _, content = parse(text, scope=_SCOPE, path=_PATH)
        assert content == "body"
