"""Each substrate case owns a copy of the literal version-1 home."""

import shutil

import pytest

from tests.contract.support import FIXTURES, Binary


@pytest.fixture
def planted(binary: Binary) -> Binary:
    shutil.copytree(FIXTURES / "home", binary.home)
    return binary
