"""The repository as strangers meet it (NI, DESIGN §28): the legal and
community floor exists, the license metadata agrees with the file, and
the installer is the shape the ruling fixed — pinned keylessly so a
missing file fails the gate, never a visitor."""

import re
import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_FLOOR = ("LICENSE", "CODE_OF_CONDUCT.md", "SECURITY.md", "CONTRIBUTING.md")
_INSTALLER = _ROOT / "scripts" / "install.sh"
_DOCKERFILE = _ROOT / "Dockerfile"


@pytest.mark.unit
@pytest.mark.parametrize("name", _FLOOR)
def test_the_floor_exists(name: str) -> None:
    assert (_ROOT / name).read_text(encoding="utf-8").strip()


@pytest.mark.unit
def test_license_metadata_matches_the_file() -> None:
    with (_ROOT / "pyproject.toml").open("rb") as f:
        assert tomllib.load(f)["project"]["license"] == "Apache-2.0"
    assert "Apache License" in (_ROOT / "LICENSE").read_text(encoding="utf-8")


@pytest.mark.unit
def test_the_installer_pins_uv_to_the_dockerfile() -> None:
    # One uv pin for the repository (ledger #185): the installer and the
    # appliance image name the same release, or a bump goes half-way.
    script = _INSTALLER.read_text(encoding="utf-8")
    pinned = re.search(r'^UV_VERSION="(\d+\.\d+\.\d+)"', script, re.M)
    assert pinned
    image = re.search(r"ghcr\.io/astral-sh/uv:(\d+\.\d+\.\d+)", _DOCKERFILE.read_text())
    assert image and image.group(1) == pinned.group(1)
    assert 'uv tool install --python ">=3.12"' in script
    commands = [ln for ln in script.splitlines() if not ln.lstrip().startswith("#")]
    assert not any("sudo" in ln for ln in commands)
    assert _INSTALLER.stat().st_mode & 0o111, "install.sh must be executable"


@pytest.mark.unit
def test_no_funding_file() -> None:
    # Ruled an explicit no (ledger #187); a FUNDING.yml is a decision, not a drop-in.
    assert not (_ROOT / ".github" / "FUNDING.yml").exists()
