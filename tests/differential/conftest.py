"""The tier's fixtures: the two doors (the binary's absence skips) and one
seeded home per session, copied fresh for every case."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests.differential import harness
from tests.differential.seed import SMALL, Seeded, seed


@pytest.fixture(scope="session")
def python_door() -> Path:
    return harness.python_door()


@pytest.fixture(scope="session")
def rust_door() -> Path:
    binary = harness.rust_door()
    if binary is None:
        pytest.skip("no binary: `cargo build` or set NEOSIAN_BINARY")
    return binary


@pytest.fixture(scope="session")
def seeded(tmp_path_factory: pytest.TempPathFactory, python_door: Path) -> Seeded:
    base = tmp_path_factory.mktemp("seed")
    return asyncio.run(seed(base / "home", SMALL, console=python_door))
