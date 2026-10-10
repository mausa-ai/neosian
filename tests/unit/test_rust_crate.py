"""The Rust crate beside the wheel (DESIGN §39, ledger #338, #348 to
#352), pinned keylessly: one version literal in two files (the wheel's
PEP 440 and the crate's semver spellings of one number), an exact
toolchain pin that `rust-version` repeats, the docs manifest's Rust twin,
and the Makefile's Rust gates run line by line in CI on the five targets
the binary ships for."""

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

import neosian
from neosian._foundation.shared.docs_assets import _TOPICS

_ROOT = Path(__file__).resolve().parents[2]
_RUNNERS = {
    "ubuntu-24.04",
    "ubuntu-24.04-arm",
    "macos-15",
    "macos-15-intel",
    "windows-2025",
}
_PAGES = re.compile(r"pages!\((.*?)\);", re.S)
_LITERAL = re.compile(r'"([a-z]+)"')


def _toml(name: str) -> dict[str, Any]:
    with (_ROOT / name).open("rb") as f:
        return tomllib.load(f)


def _semver(version: str) -> str:
    """PEP 440 to semver for the one pre-release form the release axis uses."""
    return re.sub(r"rc(\d+)$", r"-rc.\1", version)


@pytest.mark.unit
def test_the_crate_carries_the_wheels_version() -> None:
    package = _toml("Cargo.toml")["workspace"]["package"]
    assert package["version"] == _semver(neosian.__version__)
    assert _semver("1.9.0rc2") == "1.9.0-rc.2"


@pytest.mark.unit
def test_the_toolchain_is_one_exact_pin() -> None:
    channel = _toml("rust-toolchain.toml")["toolchain"]["channel"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", str(channel)), channel
    package = _toml("Cargo.toml")["workspace"]["package"]
    assert package["rust-version"] == channel
    assert (_ROOT / "Cargo.lock").is_file() and (_ROOT / "deny.toml").is_file()


@pytest.mark.unit
def test_the_docs_manifest_has_its_rust_twin() -> None:
    source = (_ROOT / "crates" / "neosian" / "src" / "docs.rs").read_text(
        encoding="utf-8"
    )
    listed = _PAGES.search(source)
    assert listed, "docs.rs lists its pages through pages!(...)"
    assert tuple(_LITERAL.findall(listed.group(1))) == _TOPICS


@pytest.mark.unit
def test_the_rust_gates_run_on_the_five_targets() -> None:
    makefile = (_ROOT / "Makefile").read_text(encoding="utf-8")
    lines = {
        ln.strip()
        for ln in makefile.splitlines()
        if ln.startswith(("\tcargo ", "\tdist "))
    }
    assert len(lines) == 5, lines
    ci = yaml.safe_load((_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    job = ci["jobs"]["rust"]
    assert set(job["strategy"]["matrix"]["runner"]) == _RUNNERS
    assert {step.get("run") for step in job["steps"]} >= lines
