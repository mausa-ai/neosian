"""Tests for the actor grammar (memory/actor.py, DESIGN §20)."""

import pytest

from neosian._foundation.memory.actor import (
    ACTOR_MAX_LENGTH,
    ACTOR_PATTERN,
    actor_matches,
    parse_actor,
)
from neosian._foundation.shared.exceptions import MemoryActorInvalidError

_EXACT_512 = "/".join(["t:" + "x" * 128] * 3 + ["t:" + "x" * 117])
_OVER_512 = "/".join(["t:" + "x" * 128] * 3 + ["t:" + "x" * 118])
assert len(_EXACT_512) == 512
assert len(_OVER_512) == 513


class TestParseActorAccepts:
    @pytest.mark.parametrize(
        "value",
        [
            "conv:thread-829",
            "conv:thread-829#4",  # the turn-ref
            "cli:local",
            "mcp:claude-code",
            "claude-code:9f1c2a",  # a hyphenated kind: client names
            "eval:dedup/session:s1",
            "claude-code:laptop/conv:x#12",  # the daemon's composition
            "user:ABC",  # ids are mixed-case; no normalization
            "user:...",  # only exactly "." and ".." are reserved
            "a:1/b:2/c:3/d:4/e:5/f:6/g:7/h:8",  # 8 segments — the ceiling
            "t:" + "i" * 128,
            _EXACT_512,
            _EXACT_512 + "#99",  # the suffix rides above the 512 limit
        ],
    )
    def test_valid_actors_round_trip(self, value: str) -> None:
        assert parse_actor(value) == value


class TestParseActorRejects:
    @pytest.mark.parametrize(
        "value",
        [
            "",
            "cli",  # the pre-NL bare kinds are no longer grammatical
            "serve",
            "thread-829#4",  # a bare conversation id
            "conv:",
            ":1",
            "Conv:1",  # kind is lowercase
            "-conv:1",  # kind starts with a letter
            "conv:a:b",  # ':' excluded from id
            "eval:a:b",  # the pre-NL eval spelling
            "conv:1#0",  # turns start at 1
            "conv:1#04",  # no leading zeros
            "conv:1#",
            "conv:1#4#5",
            "conv:1#4/cli:x",  # the suffix is terminal
            "conv:1/",
            "/conv:1",
            "conv:café",
            "conv:a b",
            "conv:1\n",  # \A…\Z anchoring
            "conv:.",
            "conv:1/cli:..",
            "a:1/b:2/c:3/d:4/e:5/f:6/g:7/h:8/i:9",  # 9 segments
            "t:" + "i" * 129,
            _OVER_512,
        ],
    )
    def test_invalid_actors_raise(self, value: str) -> None:
        with pytest.raises(MemoryActorInvalidError) as exc_info:
            parse_actor(value)
        assert exc_info.value.code == "memory_actor_invalid"
        assert exc_info.value.details == {
            "actor": value,
            "reason": exc_info.value.reason,
        }

    def test_max_length_constant(self) -> None:
        assert ACTOR_MAX_LENGTH == 512

    def test_pattern_is_newline_proof_under_plain_match(self) -> None:
        assert ACTOR_PATTERN.match("conv:1\n") is None


class TestActorMatches:
    @pytest.mark.parametrize(
        ("actor", "pattern", "expected"),
        [
            ("claude-code:abc", "claude-code:abc", True),
            ("claude-code:abc#4", "claude-code:abc", True),
            ("claude-code:abc/conv:x#2", "claude-code:abc", True),
            ("claude-code:abc/conv:x#2", "claude-code:abc/conv:x", True),
            ("claude-code:abcd", "claude-code:abc", False),  # never a string prefix
            ("conv:x/claude-code:abc", "claude-code:abc", False),  # prefix, not infix
            ("claude-code:abc", "claude-code:abc#4", False),
            ("thread-1", "thread-1", True),  # a host's bare id matches itself
            ("thread-1", "thread", False),
            (None, "claude-code:abc", False),
        ],
    )
    def test_segment_prefix_containment(
        self, actor: str | None, pattern: str, expected: bool
    ) -> None:
        assert actor_matches(actor, pattern) is expected
