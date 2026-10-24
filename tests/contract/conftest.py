"""Independent tier: run with --confcutdir=tests/contract to exclude SDK fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.contract.support import Binary, Server, executable, serve


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--storage-backend", choices=("file", "postgres", "http"), default="file"
    )
    parser.addoption("--storage-profile", choices=("full", "base"), default="full")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if Path(__file__).parent in Path(item.fspath).parents:
            item.add_marker(pytest.mark.contract)


@pytest.fixture
def binary(tmp_path: Path) -> Binary:
    return Binary(executable(), tmp_path)


@pytest.fixture
def server(binary: Binary) -> Iterator[Server]:
    with serve(binary) as running:
        yield running
