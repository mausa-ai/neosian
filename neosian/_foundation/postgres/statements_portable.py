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

from neosian._foundation.postgres.schema import statement


@dataclass(frozen=True, slots=True)
class PortableStatements:
    list_scopes: str
    list_conversations: str
    restore_scope: str
    restore_conversation: str


def build_portable_statements(schema: str) -> PortableStatements:
    return PortableStatements(
        list_scopes=statement(schema, "portable/list_scopes"),
        list_conversations=statement(schema, "portable/list_conversations"),
        restore_scope=statement(schema, "portable/restore_scope"),
        restore_conversation=statement(schema, "portable/restore_conversation"),
    )
