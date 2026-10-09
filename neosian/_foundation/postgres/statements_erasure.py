"""The eraser's statements (N8, DESIGN §38): one data-modifying CTE
blanks the selected turns and every projection entry covering one, and
records the act; one bounded SELECT reads the trail.

The ledger #34 shape: `matched` decides, both updates are filtered by
it, the trail insert fires only when something matched, and the
top-level SELECT answers the count. A selection is every turn
(`through` NULL and `all` true), the turns through `through`, or the
turns named (`all` false). `COLLATE "C"` keeps the trail's tiebreak in
codepoint order (ledger #37), the rule `erasable.newest_first` spells
for the file substrate.
"""

from __future__ import annotations

from dataclasses import dataclass

from neosian._foundation.postgres.schema import quote_identifier, validate_schema_name


@dataclass(frozen=True, slots=True)
class ErasureStatements:
    redact_turns: str
    read_turn_redactions: str


def build_erasure_statements(schema: str) -> ErasureStatements:
    s = quote_identifier(validate_schema_name(schema))

    redact_turns = f"""
        WITH matched AS (
            SELECT turn FROM {s}.turns
            WHERE conversation_id = %(conversation_id)s
              AND (%(through)s::integer IS NULL OR turn <= %(through)s)
              AND (%(all)s::boolean OR turn = ANY(%(turns)s::integer[]))
        ), blanked AS (
            UPDATE {s}.turns AS t
            SET messages = '[]'::jsonb, search_text = '', redacted = true
            WHERE t.conversation_id = %(conversation_id)s
              AND t.turn IN (SELECT turn FROM matched)
            RETURNING 1
        ), covering AS (
            UPDATE {s}.projections AS p SET text = ''
            WHERE p.conversation_id = %(conversation_id)s
              AND EXISTS (SELECT 1 FROM matched AS m
                          WHERE m.turn BETWEEN p.turn - p.span + 1 AND p.turn)
            RETURNING 1
        ), trail AS (
            INSERT INTO {s}.turn_redactions (conversation_id, turns, actor, created_at)
            SELECT %(conversation_id)s,
                   (SELECT array_agg(turn ORDER BY turn) FROM matched),
                   %(actor)s, %(now)s
            WHERE (SELECT count(*) FROM matched) > 0
            RETURNING 1
        )
        SELECT (SELECT count(*) FROM matched) AS matched
    """

    read_turn_redactions = f"""
        SELECT conversation_id, turns, actor, created_at
        FROM {s}.turn_redactions
        WHERE (%(all)s::boolean OR conversation_id = ANY(%(ids)s::text[]))
          AND (%(since)s::timestamptz IS NULL OR created_at >= %(since)s)
        ORDER BY created_at DESC, conversation_id COLLATE "C" DESC, id DESC
        LIMIT %(limit)s
    """

    return ErasureStatements(
        redact_turns=redact_turns, read_turn_redactions=read_turn_redactions
    )
