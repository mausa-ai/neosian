"""Exercise the actual build script with both native checkout line endings."""

import os
import subprocess
from pathlib import Path

import pytest

from tests.contract.support import ROOT


@pytest.fixture(scope="module")
def asset_builder(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("asset-builder")
    binary = directory / ("builder.exe" if os.name == "nt" else "builder")
    subprocess.run(
        ["rustc", str(ROOT / "crates/neosian/build.rs"), "-o", str(binary)],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return binary


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
@pytest.mark.parametrize("valid", [True, False])
def test_manifest_checks_metadata_with_either_checkout_newline(
    asset_builder: Path, tmp_path: Path, ending: str, valid: bool
) -> None:
    crate = tmp_path / "crate"
    docs = crate / "assets/docs"
    docs.mkdir(parents=True)
    output = tmp_path / "generated"
    output.mkdir()
    (docs / "topics.txt").write_bytes(("quickstart" + ending).encode())
    lines = ["---", "title: Quickstart", "summary: Start here", "---", "# Start"]
    if not valid:
        lines.remove("title: Quickstart")
    (docs / "quickstart.md").write_bytes((ending.join(lines) + ending).encode())
    result = subprocess.run(
        [str(asset_builder)],
        env={**os.environ, "CARGO_MANIFEST_DIR": str(crate), "OUT_DIR": str(output)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if valid:
        assert result.returncode == 0, result.stderr
        assert '"quickstart", include_str!' in (output / "docs_pages.rs").read_text()
    else:
        assert result.returncode != 0 and "page metadata missing" in result.stderr
        assert not (output / "docs_pages.rs").exists()
