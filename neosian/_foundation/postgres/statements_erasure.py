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

from neosian._foundation.postgres.schema import statement


@dataclass(frozen=True, slots=True)
class ErasureStatements:
    redact_turns: str
    read_turn_redactions: str


def build_erasure_statements(schema: str) -> ErasureStatements:
    return ErasureStatements(
        redact_turns=statement(schema, "erasure/redact_turns"),
        read_turn_redactions=statement(schema, "erasure/read_turn_redactions"),
    )
