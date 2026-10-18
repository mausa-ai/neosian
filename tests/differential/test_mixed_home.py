"""The mixed home (DESIGN §39.4): one implementation writes, the other
reads, both ways, against the all-Python run as the spec."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pytest

from tests.differential.corpus import BY_NAME, IDS, TURNS
from tests.differential.harness import (
    BINARY_VERBS,
    Case,
    Outcome,
    Workspace,
    run_case,
    shape_of_output,
    snapshot,
    workspace,
)
from tests.differential.seed import PROJECT_NAME, Seeded


@dataclass(frozen=True)
class Scenario:
    name: str
    writes: tuple[str, ...]
    reads: tuple[str, ...]

    @property
    def cases(self) -> tuple[Case, ...]:
        return tuple(BY_NAME[name] for name in (*self.writes, *self.reads))


_MEMORY_WRITES: Final = (
    "memory create",
    "memory str_replace",
    "memory insert",
    "memory rename",
)
SCENARIOS: Final = (
    Scenario(
        "a claude-code span, then the reads",
        writes=("claude-code prompt", "claude-code tool", "claude-code stop alone"),
        reads=(
            "claude-code session start",
            "memory index",
            "audit scope json",
            "search json",
            "continue json",
        ),
    ),
    Scenario(
        "a claude-code span, then the session start and the continue",
        writes=("claude-code prompt", "claude-code tool", "claude-code stop alone"),
        reads=(
            "claude-code session start",
            "claude-code session compact",
            "memory index",
            "search json",
            "continue json",
        ),
    ),
    Scenario(
        "a handoff round from one door links the baton, then the continue",
        writes=(
            "memory create baton",
            "claude-code prompt",
            "claude-code handoff tool",
            "claude-code stop alone",
        ),
        reads=("continue json", "claude-code session start"),
    ),
    Scenario(
        "memory writes, then the reads",
        writes=_MEMORY_WRITES,
        reads=("memory index json", "memory view document", "memory versions json"),
    ),
    Scenario(
        "memory writes, then the reads over MCP",
        writes=_MEMORY_WRITES,
        reads=("mcp reads",),
    ),
    Scenario(
        "an erasure, an undo and a deletion, then the reads",
        writes=("memory redact", "memory revert", "memory delete"),
        reads=(
            "memory index",
            "memory view document",
            "memory versions json",
            "memory versions",
        ),
    ),
    Scenario(
        "a handoff over MCP, then the session start",
        writes=("mcp handoff",),
        reads=("claude-code session start", "continue json"),
    ),
    Scenario(
        "a continue over MCP picks the baton up, then the session start",
        writes=("mcp continue named",),
        reads=("claude-code session start", "continue json", "mcp reads"),
    ),
    Scenario(
        "messages sent over MCP, then the reads and the plugin's poll",
        writes=("mcp mailbox",),
        reads=("mcp reads", "search json", "pi mailbox poll", "claude-code prompt"),
    ),
    Scenario(
        "a baton rewritten by hand, then the continue",
        writes=("memory create baton",),
        reads=("continue json", "continue", "search json"),
    ),
    Scenario(
        "the baton deleted, then the continue",
        writes=("memory delete baton",),
        reads=("continue json", "continue named"),
    ),
    Scenario(
        "a client's writes through the state process, then the reads",
        writes=("serve memory lifecycle", "serve conversation lifecycle"),
        reads=(
            "memory index",
            "memory view wire document",
            "memory versions wire json",
            "search hi json",
            "serve listings paged",
        ),
    ),
    Scenario(
        "a session's writes over /mcp, then the reads",
        writes=("serve mcp writes",),
        reads=(
            "memory index",
            "memory view mcp document",
            "memory versions mcp json",
            "serve mcp read back",
            "continue json",
        ),
    ),
)


def _run(executable: Path, cases: tuple[Case, ...], space: Workspace) -> list[Outcome]:
    return [run_case(executable, case, space) for case in cases]


def _space(seeded: Seeded, into: Path) -> Workspace:
    return workspace(
        seeded.home,
        into,
        user=seeded.user,
        proj=seeded.proj,
        session=seeded.sessions[-1],
        project_name=PROJECT_NAME,
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_each_door_reads_what_the_other_wrote(
    scenario: Scenario,
    seeded: Seeded,
    python_door: Path,
    rust_door: Path,
    tmp_path: Path,
) -> None:
    missing = sorted({case.verb for case in scenario.cases} - BINARY_VERBS)
    if missing:
        pytest.skip(f"the binary has no {', '.join(missing)} yet")
    keep = seeded.ids | IDS
    writes = tuple(BY_NAME[name] for name in scenario.writes)
    reads = tuple(BY_NAME[name] for name in scenario.reads)

    def space(name: str) -> Workspace:
        return _space(seeded, tmp_path / name)

    spec = space("spec")
    _run(python_door, writes, spec)
    expected = [
        shape_of_output(c, o, spec, keep=keep)
        for c, o in zip(reads, _run(python_door, reads, spec), strict=True)
    ]

    for writer, reader, name in (
        (python_door, rust_door, "python-writes"),
        (rust_door, python_door, "rust-writes"),
    ):
        mixed = space(name)
        _run(writer, writes, mixed)
        observed = [
            shape_of_output(c, o, mixed, keep=keep)
            for c, o in zip(reads, _run(reader, reads, mixed), strict=True)
        ]
        assert observed == expected, name
        assert snapshot(mixed, keep=keep) == snapshot(spec, keep=keep), name


def test_the_doors_take_turns_on_one_document(
    seeded: Seeded, python_door: Path, rust_door: Path, tmp_path: Path
) -> None:
    """One document through every mutation, the writer changing at each
    step: a door numbers its version from the rows the other one wrote,
    and the home ends as the all-Python run leaves it."""
    missing = sorted({case.verb for case in TURNS} - BINARY_VERBS)
    if missing:
        pytest.skip(f"the binary has no {', '.join(missing)} yet")
    keep = seeded.ids | IDS

    def run(name: str, *doors: Path) -> tuple[list[Any], dict[str, Any]]:
        space = _space(seeded, tmp_path / name)
        shapes = [
            shape_of_output(
                case, run_case(doors[index % len(doors)], case, space), space, keep=keep
            )
            for index, case in enumerate(TURNS)
        ]
        return shapes, snapshot(space, keep=keep)

    spec = run("spec", python_door)
    assert run("python-first", python_door, rust_door) == spec
    assert run("rust-first", rust_door, python_door) == spec
