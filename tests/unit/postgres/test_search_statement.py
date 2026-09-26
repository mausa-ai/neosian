"""The search statement and its pattern escaping — keyless, driver-free
(DESIGN §32)."""

import pytest

from neosian._foundation.postgres.statements import build_statements
from neosian._foundation.postgres.turn_store import like_pattern


@pytest.mark.unit
def test_the_search_statement_is_one_bounded_select() -> None:
    sql = build_statements("neosian_x").search_turns
    assert '"neosian_x".turns' in sql
    assert "ILIKE ALL(%(patterns)s::text[])" in sql
    assert "conversation_id = ANY(%(ids)s::text[])" in sql
    assert (
        'ORDER BY created_at DESC, conversation_id COLLATE "C" DESC, turn DESC' in sql
    )
    assert "LIMIT %(limit)s" in sql


@pytest.mark.unit
def test_an_append_writes_the_search_text() -> None:
    sql = build_statements("neosian_x")
    assert "search_text" in sql.append_turn
    assert "%(search_text)s" in sql.append_turn


@pytest.mark.unit
def test_like_patterns_keep_the_metacharacters_literal() -> None:
    assert like_pattern("plain") == "%plain%"
    assert like_pattern("50%_a\\b") == "%50\\%\\_a\\\\b%"
