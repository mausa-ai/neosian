"""Exercise the actual CI archive checks, including their rejected artifacts."""

import io
import os
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
@pytest.mark.parametrize("workflow", ["ci.yml", "release.yml"])
@pytest.mark.parametrize(
    ("missing", "extra", "accepted"),
    [
        (None, "neosian/assets/docs/memory.md", True),
        ("LICENSE", None, False),
        ("docs/README.md", None, False),
        (None, "docs/CHANGELOG.md", False),
        (None, "docs/README.md.bak", False),
        (None, "record/DESIGN.md", False),
        (None, ".claude/settings.json", False),
        (None, ".github/workflows/ci.yml", False),
        (None, ".import_linter_cache/cache", False),
        (None, "branding/logo.svg", False),
    ],
)
def test_archive_gate(
    tmp_path: Path,
    workflow: str,
    missing: str | None,
    extra: str | None,
    accepted: bool,
) -> None:
    config = yaml.safe_load((_ROOT / ".github/workflows" / workflow).read_text())
    command = next(
        step["run"]
        for step in config["jobs"]["build"]["steps"]
        if step.get("run", "").startswith('sdist="')
    )
    dist = tmp_path / "dist"
    dist.mkdir()
    names = {"LICENSE", "docs/README.md"} - {missing}
    if extra is not None:
        names.add(extra)
    with tarfile.open(dist / "neosian-test.tar.gz", "w:gz") as archive:
        for name in sorted(names):
            member = tarfile.TarInfo(f"neosian-test/{name}")
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", command],
        env={**os.environ, "RUNNER_TEMP": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is accepted, result.stdout + result.stderr
