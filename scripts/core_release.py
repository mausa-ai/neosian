"""Guard artifact publication, including test-only recovery of an existing tag."""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def plan(root: Path, tag: str) -> dict[str, str]:
    version = tomllib.loads((root / "Cargo.toml").read_text())["workspace"]["package"][
        "version"
    ]
    revision = git(root, "rev-parse", "HEAD")
    if tag:
        if not re.fullmatch(r"v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", tag):
            raise ValueError("release tag must be vX.Y.Z or a prerelease")
        if tag != f"v{version}":
            raise ValueError("release tag must match the workspace version")
        revision = git(root, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}")
        # Product code, assets, dependencies and build inputs must be identical.
        # Only tests and this publication machinery may repair a failed run.
        allowed = {"scripts/core_release.py", ".github/workflows/publish-core.yml"}
        changed = git(root, "diff", "--name-only", "-z", revision, "HEAD").split("\0")
        forbidden = [
            path
            for path in changed
            if path and path not in allowed and not path.startswith("tests/")
        ]
        if forbidden:
            raise ValueError(
                "product differs from release tag: " + ", ".join(forbidden)
            )
        if git(root, "status", "--porcelain", "--untracked-files=no"):
            raise ValueError("publication requires a clean checkout")
    return {
        "version": version,
        "revision": revision,
        "tag": tag,
        "publish": "true" if tag else "false",
    }


if __name__ == "__main__":
    for key, value in plan(ROOT, os.environ.get("RELEASE_TAG", "")).items():
        print(f"{key}={value}")
