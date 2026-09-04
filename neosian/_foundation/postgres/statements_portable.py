"""The mobility statements (NC4, §26): enumeration, and one verbatim
restore per unit.

A restore is one data-modifying-CTE statement, the ledger #34 shape: a
gate CTE decides "the unit is empty", every insert is filtered by it,
and the top-level SELECT reports the verdict — so the unit lands whole
or not at all, and the store still owns no transaction. Rows arrive as
one jsonb array per table; `WITH ORDINALITY … ORDER BY ord` keeps the
identity columns in archive order, which is the read order the trail
and the projections tie-break on.
"""

from __future__ import annotations

from dataclasses import dataclass

from neosian._foundation.postgres.schema import quote_identifier, validate_schema_name


@dataclass(frozen=True, slots=True)
class PortableStatements:
    list_scopes: str
    list_conversations: str
    restore_scope: str
    restore_conversation: str


def build_portable_statements(schema: str) -> PortableStatements:
    s = quote_identifier(validate_schema_name(schema))

    list_scopes = f"""
        SELECT scope FROM (
            SELECT scope FROM {s}.memories
            UNION SELECT scope FROM {s}.memory_versions
            UNION SELECT scope FROM {s}.memory_redactions
        ) AS held ORDER BY scope COLLATE "C"
    """

    list_conversations = f"""
        SELECT conversation_id FROM (
            SELECT conversation_id FROM {s}.turns
            UNION SELECT conversation_id FROM {s}.projections
        ) AS held ORDER BY conversation_id COLLATE "C"
    """

    restore_scope = f"""
        WITH gate AS (
            SELECT NOT EXISTS (SELECT 1 FROM {s}.memories WHERE scope = %(scope)s)
               AND NOT EXISTS (SELECT 1 FROM {s}.memory_versions
                               WHERE scope = %(scope)s)
               AND NOT EXISTS (SELECT 1 FROM {s}.memory_redactions
                               WHERE scope = %(scope)s) AS empty
        ), rows AS (
            INSERT INTO {s}.memory_versions
                (scope, path, version, action, content, actor, created_at,
                 redacted, neosian_format)
            SELECT %(scope)s, r.value ->> 'path', (r.value ->> 'version')::integer,
                   r.value ->> 'action', r.value ->> 'content', r.value ->> 'actor',
                   (r.value ->> 'created_at')::timestamptz,
                   (r.value ->> 'redacted')::boolean, %(format)s
            FROM jsonb_array_elements(%(versions)s::jsonb) AS r(value)
            WHERE (SELECT empty FROM gate)
            RETURNING 1
        ), docs AS (
            INSERT INTO {s}.memories
                (scope, path, content, version, created_at, updated_at, actor,
                 redacted, neosian_format, extra)
            SELECT %(scope)s, d.value ->> 'path', d.value ->> 'content',
                   (d.value ->> 'version')::integer,
                   (d.value ->> 'created_at')::timestamptz,
                   (d.value ->> 'updated_at')::timestamptz, d.value ->> 'actor',
                   (d.value ->> 'redacted')::boolean, %(format)s,
                   COALESCE(d.value -> 'extra', '{{}}'::jsonb)
            FROM jsonb_array_elements(%(documents)s::jsonb) AS d(value)
            WHERE (SELECT empty FROM gate)
            RETURNING 1
        ), acts AS (
            INSERT INTO {s}.memory_redactions (scope, path, actor, count, created_at)
            SELECT %(scope)s, a.value ->> 'path', a.value ->> 'actor',
                   (a.value ->> 'count')::integer,
                   (a.value ->> 'created_at')::timestamptz
            FROM jsonb_array_elements(%(redactions)s::jsonb)
                 WITH ORDINALITY AS a(value, ord)
            WHERE (SELECT empty FROM gate)
            ORDER BY a.ord
            RETURNING 1
        )
        SELECT empty FROM gate
    """

    restore_conversation = f"""
        WITH gate AS (
            SELECT NOT EXISTS (SELECT 1 FROM {s}.turns
                               WHERE conversation_id = %(conversation_id)s)
               AND NOT EXISTS (SELECT 1 FROM {s}.projections
                               WHERE conversation_id = %(conversation_id)s) AS empty
        ), parent AS (
            INSERT INTO {s}.conversations (conversation_id, created_at)
            SELECT %(conversation_id)s, %(now)s
            WHERE (SELECT empty FROM gate)
            ON CONFLICT (conversation_id) DO NOTHING
        ), rows AS (
            INSERT INTO {s}.turns
                (conversation_id, turn, messages, created_at, neosian_format, actor)
            SELECT %(conversation_id)s, (t.value ->> 'turn')::integer,
                   t.value -> 'messages', (t.value ->> 'created_at')::timestamptz,
                   %(format)s, t.value ->> 'actor'
            FROM jsonb_array_elements(%(turns)s::jsonb) AS t(value)
            WHERE (SELECT empty FROM gate)
            RETURNING 1
        ), entries AS (
            INSERT INTO {s}.projections
                (conversation_id, turn, kind, text, span, neosian_format)
            SELECT %(conversation_id)s, (p.value ->> 'turn')::integer,
                   p.value ->> 'kind', p.value ->> 'text',
                   (p.value ->> 'span')::integer, %(format)s
            FROM jsonb_array_elements(%(projections)s::jsonb)
                 WITH ORDINALITY AS p(value, ord)
            WHERE (SELECT empty FROM gate)
            ORDER BY p.ord
            RETURNING 1
        )
        SELECT empty FROM gate
    """

    return PortableStatements(
        list_scopes=list_scopes,
        list_conversations=list_conversations,
        restore_scope=restore_scope,
        restore_conversation=restore_conversation,
    )
