"""The eraser's statements and the rows they touch — keyless, driver-free
(N8, DESIGN §38)."""

import pytest

from neosian._foundation.postgres.statements import build_statements
from neosian._foundation.postgres.statements_erasure import build_erasure_statements
from neosian._foundation.postgres.statements_portable import build_portable_statements


@pytest.mark.unit
def test_redact_turns_is_one_statement_over_both_tables_and_the_trail() -> None:
    sql = build_erasure_statements("neosian_x").redact_turns
    assert sql.count('"neosian_x".turns') == 2
    assert "SET messages = '[]'::jsonb, search_text = '', redacted = true" in sql
    assert "\"neosian_x\".projections AS p SET text = ''" in sql
    assert "m.turn BETWEEN p.turn - p.span + 1 AND p.turn" in sql
    assert "%(through)s::integer IS NULL OR turn <= %(through)s" in sql
    assert "%(all)s::boolean OR turn = ANY(%(turns)s::integer[])" in sql
    assert 'INSERT INTO "neosian_x".turn_redactions' in sql
    assert "array_agg(turn ORDER BY turn)" in sql
    assert "WHERE (SELECT count(*) FROM matched) > 0" in sql
    assert sql.strip().endswith("SELECT (SELECT count(*) FROM matched) AS matched")


@pytest.mark.unit
def test_the_trail_read_is_bounded_newest_first_in_codepoint_order() -> None:
    sql = build_erasure_statements("neosian_x").read_turn_redactions
    assert "%(all)s::boolean OR conversation_id = ANY(%(ids)s::text[])" in sql
    assert "%(since)s::timestamptz IS NULL OR created_at >= %(since)s" in sql
    assert 'ORDER BY created_at DESC, conversation_id COLLATE "C" DESC, id DESC' in sql
    assert "LIMIT %(limit)s" in sql


@pytest.mark.unit
def test_every_turn_read_carries_the_flag() -> None:
    sql = build_statements("neosian_x")
    assert "actor, redacted" in sql.read_turns
    assert "redacted" in sql.search_turns


@pytest.mark.unit
def test_a_restore_writes_the_skeleton_and_the_trail() -> None:
    sql = build_portable_statements("neosian_x").restore_conversation
    assert "COALESCE((t.value ->> 'redacted')::boolean, false)" in sql
    assert 'INSERT INTO "neosian_x".turn_redactions' in sql
    assert "jsonb_array_elements_text(a.value -> 'turns')" in sql
