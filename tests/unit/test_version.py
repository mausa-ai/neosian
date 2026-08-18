"""Pins the version derivation: one literal in pyproject, everything follows."""

import importlib.metadata
import tomllib
from pathlib import Path

import neosian

PYPROJECT = Path(__file__).parents[2] / "pyproject.toml"


def test_version_derives_from_installed_metadata() -> None:
    assert neosian.__version__ == importlib.metadata.version("neosian")


def test_pyproject_is_the_single_source() -> None:
    with PYPROJECT.open("rb") as f:
        pyproject = tomllib.load(f)
    assert neosian.__version__ == pyproject["project"]["version"]
    assert "version" not in pyproject["project"].get("dynamic", [])
