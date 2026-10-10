"""Every corpus case through both doors over fresh copies of one home:
what each printed and what each left behind, by parity's unit."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.differential.corpus import CASES, IDS
from tests.differential.harness import (
    BINARY_VERBS,
    Case,
    run_case,
    shape_of_output,
    snapshot,
    workspace,
)
from tests.differential.seed import PROJECT_NAME, Seeded


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_both_doors_agree(
    case: Case, seeded: Seeded, python_door: Path, rust_door: Path, tmp_path: Path
) -> None:
    if case.verb not in BINARY_VERBS:
        pytest.skip(f"the binary has no `{case.verb}` yet")
    keep = seeded.ids | IDS
    spaces = {
        door: workspace(
            seeded.home,
            tmp_path / door,
            user=seeded.user,
            proj=seeded.proj,
            project_name=PROJECT_NAME,
        )
        for door in ("python", "rust")
    }
    python = run_case(python_door, case, spaces["python"])
    rust = run_case(rust_door, case, spaces["rust"])
    assert shape_of_output(case, rust, spaces["rust"], keep=keep) == shape_of_output(
        case, python, spaces["python"], keep=keep
    )
    assert snapshot(spaces["rust"], keep=keep) == snapshot(spaces["python"], keep=keep)
