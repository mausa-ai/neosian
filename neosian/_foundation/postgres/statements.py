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

from neosian._foundation.postgres.schema import statement


@dataclass(frozen=True, slots=True)
class Statements:
    """The store's SQL, qualified with one quoted schema name."""

    read_document: str
    write_document: str
    delete_document: str
    rename_document: str
    list_documents: str
    read_versions: str
    read_history: str
    read_redactions: str
    redact: str
    append_turn: str
    read_turns: str
    last_turn_number: str
    search_turns: str
    append_projections: str
    read_projections: str


def build_statements(schema: str) -> Statements:
    return Statements(
        read_document=statement(schema, "store/read_document"),
        write_document=statement(schema, "store/write_document"),
        delete_document=statement(schema, "store/delete_document"),
        rename_document=statement(schema, "store/rename_document"),
        list_documents=statement(schema, "store/list_documents"),
        read_versions=statement(schema, "store/read_versions"),
        read_history=statement(schema, "store/read_history"),
        read_redactions=statement(schema, "store/read_redactions"),
        redact=statement(schema, "store/redact"),
        append_turn=statement(schema, "store/append_turn"),
        read_turns=statement(schema, "store/read_turns"),
        last_turn_number=statement(schema, "store/last_turn_number"),
        search_turns=statement(schema, "store/search_turns"),
        append_projections=statement(schema, "store/append_projections"),
        read_projections=statement(schema, "store/read_projections"),
    )
