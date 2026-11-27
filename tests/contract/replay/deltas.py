"""The reviewed, intentional differences from the frozen expectations.

`expected.json` is the released behavior and never changes. `deltas.json`
lists each answer a ledger decision changed on purpose: the case, the path
of the value inside its expected output, the new literal, and the decision.
Only #448 is listed: `recall_turn` gained `view`, `seq` and `part`, its
default answer became the light view, and its description and schema name
the new inputs; `continue_session` gained `view`, whose default is its
released answer, so only its description and schema are listed;
`search_history` gained `kinds` and record hits, whose answers on the plain
store this corpus records to are the released ones, so only its description
and schema are listed. The replay comparison first compares each listed
value with its literal as written, then replaces exactly these values and
nothing else, so any other difference fails it. `test_deltas.py` allows an
entry only on the cases and paths it names.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
DELTAS: list[dict[str, Any]] = json.loads((HERE / "deltas.json").read_text())
DECISIONS = frozenset({"#448"})


def located(value: Any, path: list[Any]) -> tuple[Any, Any]:
    """The container holding the value at `path`, and its last key."""
    for key in path[:-1]:
        value = value[key]
    last = path[-1]
    if isinstance(value, list):
        assert isinstance(last, int) and 0 <= last < len(value), path
    else:
        assert isinstance(value, dict) and last in value, path
    return value, last


def expected_for(name: str, frozen: Any) -> Any:
    """The frozen expectation of case `name` with its listed deltas only."""
    result = copy.deepcopy(frozen)
    for delta in DELTAS:
        if delta["case"] != name:
            continue
        container, key = located(result, delta["at"])
        assert container[key] != delta["literal"], f"stale delta: {delta['at']}"
        container[key] = copy.deepcopy(delta["literal"])
    return result
