"""Independent tier: run with --confcutdir=tests/contract to exclude SDK fixtures."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.contract.support import ROOT, Binary, Server, serve


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if Path(__file__).parent in Path(item.fspath).parents:
            item.add_marker(pytest.mark.contract)


@pytest.fixture
def binary(tmp_path: Path) -> Binary:
    suffix = ".exe" if os.name == "nt" else ""
    executable = Path(
        os.environ.get("NEOSIAN_BINARY", ROOT / "target" / "debug" / f"neosian{suffix}")
    ).resolve()
    assert executable.is_file(), "build the binary or set NEOSIAN_BINARY"
    return Binary(executable, tmp_path)


@pytest.fixture
def server(binary: Binary) -> Iterator[Server]:
    with serve(binary) as running:
        yield running
