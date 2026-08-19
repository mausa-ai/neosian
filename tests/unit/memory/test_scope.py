"""Tests for the scope grammar (memory/scope.py, ECOSYSTEM §2)."""

import pytest

from neosian._foundation.memory.scope import (
    SCOPE_MAX_LENGTH,
    SCOPE_PATTERN,
    parse_scope,
    scope_directory,
    scope_segments,
)
from neosian._foundation.shared.exceptions import MemoryScopeInvalidError

# Boundary scopes built programmatically, not eyeballed: exactly 512
# chars with every segment inside the grammar, and 513 for the rejection.
_EXACT_512 = "/".join(["t:" + "x" * 128] * 3 + ["t:" + "x" * 117])
_OVER_512 = "/".join(["t:" + "x" * 128] * 3 + ["t:" + "x" * 118])
assert len(_EXACT_512) == 512
assert len(_OVER_512) == 513
_EIGHT_SEGMENTS = "/".join(["t:" + "x" * 61] * 8)
assert len(_EIGHT_SEGMENTS) == 511


class TestParseScopeAccepts:
    @pytest.mark.parametrize(
        "value",
        [
            "user:123",
            "user:1234-5678-abcd",
            "org:acme",
            "user:123/proj:erp",
            "tenant:acme/kb:main",
            "user:ABC",  # ids are mixed-case; no normalization
            "user:a.b",  # dots inside an id are legal
            "user:...",  # only exactly "." and ".." are reserved
            "user:..foo",
            "a:1/b:2/c:3/d:4/e:5/f:6/g:7/h:8",  # 8 segments — the ceiling
            "type_32_chars_long_aaaaaaaaaaaaa:x",  # 32-char type
            "t:" + "i" * 128,  # 128-char id
            _EIGHT_SEGMENTS,
            _EXACT_512,
        ],
    )
    def test_valid_scopes_round_trip(self, value: str) -> None:
        assert parse_scope(value) == value


class TestParseScopeRejects:
    @pytest.mark.parametrize(
        "value",
        [
            "",
            "user",  # no colon
            "user:",  # empty id
            ":1",  # empty type
            "/user:1",  # leading slash
            "user:1/",  # trailing slash
            "user:1//p:2",  # empty segment
            "User:1",  # type must be lowercase
            "1user:1",  # type must start with a letter
            "user:a:b",  # ':' excluded from id
            "user:café",  # literal charset, never \w
            "user:a b",  # whitespace
            " user:1",
            "user:1 ",
            "user:1\n",  # \A…\Z anchoring — '$' would accept this
            "user:1\r",
            "user:.",  # reserved id
            "user:..",
            "user:1/proj:..",
            "a:1/b:2/c:3/d:4/e:5/f:6/g:7/h:8/i:9",  # 9 segments
            "type_33_chars_long_aaaaaaaaaaaaaa:x",  # 33-char type
            "t:" + "i" * 129,  # 129-char id
            _OVER_512,  # 513 chars, every segment individually legal
        ],
    )
    def test_invalid_scopes_raise(self, value: str) -> None:
        with pytest.raises(MemoryScopeInvalidError) as exc_info:
            parse_scope(value)
        assert exc_info.value.code == "memory_scope_invalid"
        assert exc_info.value.details == {
            "scope": value,
            "reason": exc_info.value.reason,
        }

    def test_max_length_constant(self) -> None:
        assert SCOPE_MAX_LENGTH == 512

    def test_pattern_is_newline_proof_under_plain_match(self) -> None:
        # SCOPE_PATTERN is exported; hosts will call .match() on it.
        assert SCOPE_PATTERN.match("user:1\n") is None


class TestScopeSegments:
    def test_decomposes_into_type_id_pairs(self) -> None:
        scope = parse_scope("user:123/proj:erp.v2")
        assert scope_segments(scope) == (("user", "123"), ("proj", "erp.v2"))


class TestScopeDirectory:
    def test_one_component_per_segment_each_carrying_percent_3a(self) -> None:
        scope = parse_scope("user:123/proj:erp")
        components = scope_directory(scope)
        assert components == ("user%3A123", "proj%3Aerp")
        assert all("%3A" in c for c in components)

    def test_max_length_scope_stays_under_filesystem_name_limits(self) -> None:
        for boundary in (_EXACT_512, _EIGHT_SEGMENTS):
            components = scope_directory(parse_scope(boundary))
            assert all(len(c) < 255 for c in components)
