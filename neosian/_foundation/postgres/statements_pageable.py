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

from neosian._foundation.postgres.schema import statement


@dataclass(frozen=True, slots=True)
class PageableStatements:
    list_documents: str
    read_versions: str
    read_history: str
    read_redactions: str


def build_pageable_statements(schema: str) -> PageableStatements:
    return PageableStatements(
        list_documents=statement(schema, "pageable/list_documents"),
        read_versions=statement(schema, "pageable/read_versions"),
        read_history=statement(schema, "pageable/read_history"),
        read_redactions=statement(schema, "pageable/read_redactions"),
    )
