"""Every SQL statement, built once per schema.

Mutations are single data-modifying-CTE statements: gate CTEs suppress
the writes when a precondition fails, and the top-level SELECT returns
both the written row and the pre-state diagnostics — one round trip
decides mutate-or-raise, and the store never owns a transaction
(ledger #34). Version/turn-number races surface as unique-violations on
the primary keys and are retried by the pool with a fresh snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass

from neosian._foundation.postgres.schema import quote_identifier, validate_schema_name


@dataclass(frozen=True, slots=True)
class Statements:
    """The store's SQL, qualified with one quoted schema name."""

    read_document: str
    write_document: str
    delete_document: str
    rename_document: str
    list_documents: str
    read_versions: str
    redact: str
    append_turn: str
    read_turns: str
    last_turn_number: str
    append_projections: str
    read_projections: str


def build_statements(schema: str) -> Statements:
    s = quote_identifier(validate_schema_name(schema))

    read_document = f"""
        SELECT content, version, created_at, updated_at, actor, redacted,
               neosian_format, extra
        FROM {s}.memories
        WHERE scope = %(scope)s AND path = %(path)s
    """

    # `current` is the pre-state; `gate` decides; `next` continues from the
    # maximum version ever issued (GREATEST covers a hand-planted document
    # with no version rows). The doc upsert keeps created_at and extra.
    write_document = f"""
        WITH current AS (
            SELECT version, neosian_format FROM {s}.memories
            WHERE scope = %(scope)s AND path = %(path)s
        ), gate AS (
            SELECT
                (SELECT count(*) FROM current) > 0 AS existed,
                COALESCE((SELECT neosian_format FROM current), %(format)s)
                    <= %(format)s AS format_ok,
                (%(expected)s::integer IS NULL
                 OR (SELECT version FROM current)
                    IS NOT DISTINCT FROM %(expected)s::integer) AS version_ok
        ), next AS (
            SELECT GREATEST(
                COALESCE((SELECT max(version) FROM {s}.memory_versions
                          WHERE scope = %(scope)s AND path = %(path)s), 0),
                COALESCE((SELECT version FROM current), 0)
            ) + 1 AS version
        ), vrow AS (
            INSERT INTO {s}.memory_versions
                (scope, path, version, action, content, actor, created_at,
                 neosian_format)
            SELECT %(scope)s, %(path)s, next.version,
                   CASE WHEN g.existed THEN 'modified' ELSE 'created' END,
                   %(content)s, %(actor)s, %(now)s, %(format)s
            FROM next, gate AS g
            WHERE g.format_ok AND g.version_ok
            RETURNING version
        ), doc AS (
            INSERT INTO {s}.memories
                (scope, path, content, version, created_at, updated_at,
                 actor, redacted, neosian_format)
            SELECT %(scope)s, %(path)s, %(content)s, vrow.version,
                   %(now)s, %(now)s, %(actor)s, false, %(format)s
            FROM vrow
            ON CONFLICT (scope, path) DO UPDATE SET
                content = EXCLUDED.content,
                version = EXCLUDED.version,
                updated_at = EXCLUDED.updated_at,
                actor = EXCLUDED.actor,
                redacted = false,
                neosian_format = EXCLUDED.neosian_format
            RETURNING version, created_at, updated_at, extra
        )
        SELECT g.existed, g.format_ok, g.version_ok,
               (SELECT version FROM current) AS current_version,
               (SELECT neosian_format FROM current) AS current_format,
               d.version, d.created_at, d.updated_at, d.extra
        FROM gate AS g LEFT JOIN doc AS d ON true
    """

    delete_document = f"""
        WITH current AS (
            SELECT content, version, redacted, neosian_format
            FROM {s}.memories
            WHERE scope = %(scope)s AND path = %(path)s
        ), gate AS (
            SELECT
                (SELECT count(*) FROM current) > 0 AS existed,
                COALESCE((SELECT neosian_format FROM current), %(format)s)
                    <= %(format)s AS format_ok
        ), next AS (
            SELECT GREATEST(
                COALESCE((SELECT max(version) FROM {s}.memory_versions
                          WHERE scope = %(scope)s AND path = %(path)s), 0),
                COALESCE((SELECT version FROM current), 0)
            ) + 1 AS version
        ), vrow AS (
            INSERT INTO {s}.memory_versions
                (scope, path, version, action, content, actor, created_at,
                 redacted, neosian_format)
            SELECT %(scope)s, %(path)s, next.version, 'deleted',
                   c.content, %(actor)s, %(now)s, c.redacted, %(format)s
            FROM next, current AS c, gate AS g
            WHERE g.existed AND g.format_ok
            RETURNING version
        ), removal AS (
            DELETE FROM {s}.memories AS m
            WHERE m.scope = %(scope)s AND m.path = %(path)s
              AND EXISTS (SELECT 1 FROM vrow)
            RETURNING 1
        )
        SELECT g.existed, g.format_ok,
               (SELECT neosian_format FROM current) AS current_format
        FROM gate AS g
    """

    # src gains a 'deleted' row, dst a 'created' one continuing dst's own
    # history; the dst insert is deliberately conflict-free — a concurrent
    # creator aborts the statement and the retry reports the conflict.
    rename_document = f"""
        WITH src AS (
            SELECT content, version, redacted, neosian_format, extra
            FROM {s}.memories
            WHERE scope = %(scope)s AND path = %(src)s
        ), dst AS (
            SELECT version FROM {s}.memories
            WHERE scope = %(scope)s AND path = %(dst)s
        ), gate AS (
            SELECT
                (SELECT count(*) FROM src) > 0 AS src_exists,
                (SELECT count(*) FROM dst) > 0 AS dst_exists,
                COALESCE((SELECT neosian_format FROM src), %(format)s)
                    <= %(format)s AS format_ok
        ), go AS (
            SELECT src_exists AND NOT dst_exists AND format_ok AS ok FROM gate
        ), src_next AS (
            SELECT GREATEST(
                COALESCE((SELECT max(version) FROM {s}.memory_versions
                          WHERE scope = %(scope)s AND path = %(src)s), 0),
                COALESCE((SELECT version FROM src), 0)
            ) + 1 AS version
        ), dst_next AS (
            SELECT COALESCE((SELECT max(version) FROM {s}.memory_versions
                             WHERE scope = %(scope)s AND path = %(dst)s), 0)
                   + 1 AS version
        ), src_row AS (
            INSERT INTO {s}.memory_versions
                (scope, path, version, action, content, actor, created_at,
                 redacted, neosian_format)
            SELECT %(scope)s, %(src)s, src_next.version, 'deleted',
                   src.content, %(actor)s, %(now)s, src.redacted, %(format)s
            FROM src_next, src
            WHERE (SELECT ok FROM go)
            RETURNING version
        ), dst_row AS (
            INSERT INTO {s}.memory_versions
                (scope, path, version, action, content, actor, created_at,
                 redacted, neosian_format)
            SELECT %(scope)s, %(dst)s, dst_next.version, 'created',
                   src.content, %(actor)s, %(now)s, src.redacted, %(format)s
            FROM dst_next, src
            WHERE (SELECT ok FROM go)
            RETURNING version
        ), doc AS (
            INSERT INTO {s}.memories
                (scope, path, content, version, created_at, updated_at,
                 actor, redacted, neosian_format, extra)
            SELECT %(scope)s, %(dst)s, src.content, dst_row.version,
                   %(now)s, %(now)s, %(actor)s, src.redacted, %(format)s,
                   src.extra
            FROM dst_row, src
            RETURNING content, version, created_at, updated_at, redacted,
                      extra
        ), removal AS (
            DELETE FROM {s}.memories AS m
            WHERE m.scope = %(scope)s AND m.path = %(src)s
              AND EXISTS (SELECT 1 FROM doc)
            RETURNING 1
        )
        SELECT g.src_exists, g.dst_exists, g.format_ok,
               (SELECT neosian_format FROM src) AS current_format,
               d.content, d.version, d.created_at, d.updated_at,
               d.redacted, d.extra
        FROM gate AS g LEFT JOIN doc AS d ON true
    """

    # COLLATE "C" pins codepoint order regardless of server collation
    # (ledger #37); the LIKE pattern is escaped in Python.
    list_documents = f"""
        SELECT path, version, created_at, updated_at, redacted,
               neosian_format
        FROM {s}.memories
        WHERE scope = %(scope)s AND path LIKE %(pattern)s ESCAPE '\\'
        ORDER BY path COLLATE "C"
    """

    read_versions = f"""
        SELECT path, version, action, content, actor, created_at, redacted,
               neosian_format
        FROM {s}.memory_versions
        WHERE scope = %(scope)s AND path = %(path)s
        ORDER BY version DESC
        LIMIT %(limit)s
    """

    # Targets = every path with a current row or history; both updates
    # clear content in place (updated_at untouched, C3); the erasure
    # trail records the act when anything matched.
    redact = f"""
        WITH targets AS (
            SELECT path FROM {s}.memories
            WHERE scope = %(scope)s
              AND (%(path)s::text IS NULL OR path = %(path)s)
            UNION
            SELECT path FROM {s}.memory_versions
            WHERE scope = %(scope)s
              AND (%(path)s::text IS NULL OR path = %(path)s)
        ), doc_update AS (
            UPDATE {s}.memories AS m SET content = '', redacted = true
            WHERE m.scope = %(scope)s
              AND m.path IN (SELECT path FROM targets)
            RETURNING 1
        ), version_update AS (
            UPDATE {s}.memory_versions AS v SET content = '', redacted = true
            WHERE v.scope = %(scope)s
              AND v.path IN (SELECT path FROM targets)
            RETURNING 1
        ), trail AS (
            INSERT INTO {s}.memory_redactions
                (scope, path, actor, count, created_at)
            SELECT %(scope)s, %(path)s, %(actor)s,
                   (SELECT count(*) FROM targets), %(now)s
            WHERE (SELECT count(*) FROM targets) > 0
            RETURNING 1
        )
        SELECT (SELECT count(*) FROM targets) AS matched
    """

    # CS3 verbatim: COALESCE(MAX(turn),0)+1 under UNIQUE(conversation_id,
    # turn), retried on collision. The parent upsert lands in the same
    # statement; the FK is deferred, so ordering can never trip it.
    append_turn = f"""
        WITH parent AS (
            INSERT INTO {s}.conversations (conversation_id, created_at)
            VALUES (%(conversation_id)s, %(now)s)
            ON CONFLICT (conversation_id) DO NOTHING
        ), next AS (
            SELECT COALESCE(MAX(turn), 0) + 1 AS turn
            FROM {s}.turns WHERE conversation_id = %(conversation_id)s
        )
        INSERT INTO {s}.turns
            (conversation_id, turn, messages, created_at, neosian_format, actor)
        SELECT %(conversation_id)s, next.turn, %(messages)s::jsonb,
               %(now)s, %(format)s, %(actor)s
        FROM next
        RETURNING turn
    """

    read_turns = f"""
        SELECT turn, messages, created_at, neosian_format, actor
        FROM {s}.turns
        WHERE conversation_id = %(conversation_id)s AND turn > %(after)s
        ORDER BY turn
        LIMIT %(limit)s
    """

    last_turn_number = f"""
        SELECT COALESCE(MAX(turn), 0)
        FROM {s}.turns WHERE conversation_id = %(conversation_id)s
    """

    # WITH ORDINALITY keeps batch order, so the identity column realizes
    # insertion order — the (turn, span, id) read order of §9.6.
    append_projections = f"""
        WITH parent AS (
            INSERT INTO {s}.conversations (conversation_id, created_at)
            VALUES (%(conversation_id)s, %(now)s)
            ON CONFLICT (conversation_id) DO NOTHING
        )
        INSERT INTO {s}.projections
            (conversation_id, turn, kind, text, span, neosian_format)
        SELECT %(conversation_id)s,
               (entry.value ->> 'turn')::integer,
               entry.value ->> 'kind',
               entry.value ->> 'text',
               (entry.value ->> 'span')::integer,
               %(format)s
        FROM jsonb_array_elements(%(entries)s::jsonb)
             WITH ORDINALITY AS entry(value, ord)
        ORDER BY entry.ord
    """

    read_projections = f"""
        SELECT turn, kind, text, span, neosian_format
        FROM {s}.projections
        WHERE conversation_id = %(conversation_id)s AND turn > %(after)s
        ORDER BY turn, span, id
        LIMIT %(limit)s
    """

    return Statements(
        read_document=read_document,
        write_document=write_document,
        delete_document=delete_document,
        rename_document=rename_document,
        list_documents=list_documents,
        read_versions=read_versions,
        redact=redact,
        append_turn=append_turn,
        read_turns=read_turns,
        last_turn_number=last_turn_number,
        append_projections=append_projections,
        read_projections=read_projections,
    )
