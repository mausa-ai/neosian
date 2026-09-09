"""The repository as strangers meet it (NI, DESIGN §28; the floor trimmed
at NX, ledger #199): the three floor files exist, SECURITY.md names the
channel, the license metadata agrees with the file, and the installer is
the shape the ruling fixed — pinned keylessly so a missing file fails the
gate, never a visitor."""

import re
import tomllib
from pathlib import Path

import pytest

import neosian

_ROOT = Path(__file__).resolve().parents[2]
_FLOOR = ("LICENSE", "README.md", "SECURITY.md")
_INSTALLER = _ROOT / "scripts" / "install.sh"
_DOCKERFILE = _ROOT / "Dockerfile"


@pytest.mark.unit
@pytest.mark.parametrize("name", _FLOOR)
def test_the_floor_exists(name: str) -> None:
    assert (_ROOT / name).read_text(encoding="utf-8").strip()


@pytest.mark.unit
def test_security_names_the_channel_and_the_address() -> None:
    # Private vulnerability reporting first, the one library address by
    # email (ledger #186, #199); the README carries conduct and the DCO.
    policy = (_ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "private vulnerability reporting" in policy
    assert "community@neosian.com" in policy
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    assert "git commit -s" in readme
    assert "community@neosian.com" in readme


@pytest.mark.unit
def test_the_record_is_not_here() -> None:
    # The roadmap, the design record, the tour and the agent procedures
    # live in a private sibling repository (ledger #198, #200); the public
    # tree carries AGENTS.md and a CLAUDE.md that only defers to it.
    assert not (_ROOT / "docs").exists()
    assert not (_ROOT / ".claude" / "commands").exists()
    assert (_ROOT / "CLAUDE.md").read_text(encoding="utf-8") == "@AGENTS.md\n"
    agents = (_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.strip() and not any(ln.startswith("@") for ln in agents.splitlines())


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
    assert 'PACKAGE="neosian"' in script
    assert 'uv tool install --python ">=3.12"' in script
    # The install says what it brings, before it does (ledger #206).
    assert script.index("is one package") < script.index("uv tool install --python")
    # The default pin is the release the script shipped with (ledger #205):
    # uv refuses an unpinned pre-release while a yanked final exists.
    release = re.search(r'^NEOSIAN_RELEASE="([^"]+)"', script, re.M)
    assert release and release.group(1) == neosian.__version__
    commands = [ln for ln in script.splitlines() if not ln.lstrip().startswith("#")]
    assert not any("sudo" in ln for ln in commands)
    assert _INSTALLER.stat().st_mode & 0o111, "install.sh must be executable"


@pytest.mark.unit
def test_no_funding_file() -> None:
    # Ruled an explicit no (ledger #187); a FUNDING.yml is a decision, not a drop-in.
    assert not (_ROOT / ".github" / "FUNDING.yml").exists()
