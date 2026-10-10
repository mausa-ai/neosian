"""The mixed home (DESIGN §39.4): one implementation writes, the other
reads, both ways, against the all-Python run as the spec."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

from tests.differential.corpus import BY_NAME, IDS
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
        "memory writes, then the reads",
        writes=(
            "memory create",
            "memory str_replace",
            "memory insert",
            "memory rename",
        ),
        reads=(
            "memory index json",
            "memory view document",
            "memory versions json",
            "mcp reads",
        ),
    ),
    Scenario(
        "a handoff over MCP, then the session start",
        writes=("mcp handoff",),
        reads=("claude-code session start", "continue json"),
    ),
)


def _run(executable: Path, cases: tuple[Case, ...], space: Workspace) -> list[Outcome]:
    return [run_case(executable, case, space) for case in cases]


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
        return workspace(
            seeded.home,
            tmp_path / name,
            user=seeded.user,
            proj=seeded.proj,
            project_name=PROJECT_NAME,
        )

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
