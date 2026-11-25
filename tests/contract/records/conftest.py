"""Each records case owns a FileStore whose marker is written here by hand."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.contract.records.support import enable
from tests.contract.support import Binary, Server, serve


@pytest.fixture
def enabled(binary: Binary) -> Binary:
    enable(binary)
    return binary


@pytest.fixture
def records(enabled: Binary) -> Iterator[Server]:
    with serve(enabled) as running:
        yield running
