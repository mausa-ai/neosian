"""The paged listings' statements (NQ2 slice C, §8): each is its ABC
read with a keyset predicate and one row more than the page, so the
store knows whether anything follows without a second query.

The keysets are the orders the ABC reads already use — `path COLLATE
"C"`, `version DESC`, the ledger's `(created_at DESC, path, version
DESC)`, and the trail's `(created_at DESC, id DESC)`; a NULL keyset is
the first page.
"""

from __future__ import annotations

from dataclasses import dataclass

from neosian._foundation.postgres.schema import quote_identifier, validate_schema_name


@dataclass(frozen=True, slots=True)
class PageableStatements:
    list_documents: str
    read_versions: str
    read_history: str
    read_redactions: str


def build_pageable_statements(schema: str) -> PageableStatements:
    s = quote_identifier(validate_schema_name(schema))

    list_documents = f"""
        SELECT path, version, created_at, updated_at, redacted,
               neosian_format
        FROM {s}.memories
        WHERE scope = %(scope)s AND path LIKE %(pattern)s ESCAPE '\\'
          AND (%(path)s::text IS NULL OR path COLLATE "C" > %(path)s)
        ORDER BY path COLLATE "C"
        LIMIT %(limit)s
    """

    read_versions = f"""
        SELECT path, version, action, content, actor, created_at, redacted,
               neosian_format
        FROM {s}.memory_versions
        WHERE scope = %(scope)s AND path = %(path)s
          AND (%(version)s::integer IS NULL OR version < %(version)s)
        ORDER BY version DESC
        LIMIT %(limit)s
    """

    read_history = f"""
        SELECT path, version, action, content, actor, created_at, redacted,
               neosian_format
        FROM {s}.memory_versions
        WHERE scope = %(scope)s
          AND (%(since)s::timestamptz IS NULL OR created_at >= %(since)s)
          AND (
            %(at)s::timestamptz IS NULL
            OR created_at < %(at)s
            OR (created_at = %(at)s AND (
                path COLLATE "C" > %(path)s::text
                OR (path = %(path)s AND version < %(version)s::integer)
            ))
          )
        ORDER BY created_at DESC, path COLLATE "C", version DESC
        LIMIT %(limit)s
    """

    read_redactions = f"""
        SELECT path, actor, created_at, count, id
        FROM {s}.memory_redactions
        WHERE scope = %(scope)s
          AND (%(since)s::timestamptz IS NULL OR created_at >= %(since)s)
          AND (
            %(at)s::timestamptz IS NULL
            OR (created_at, id) < (%(at)s, %(id)s::bigint)
          )
        ORDER BY created_at DESC, id DESC
        LIMIT %(limit)s
    """

    return PageableStatements(
        list_documents=list_documents,
        read_versions=read_versions,
        read_history=read_history,
        read_redactions=read_redactions,
    )
