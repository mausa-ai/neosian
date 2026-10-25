"""Build deterministic, versioned docs and HTTP conformance release artifacts."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import tarfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(destination: Path, root: Path = ROOT) -> list[Path]:
    version = tomllib.loads((root / "Cargo.toml").read_text())["workspace"]["package"][
        "version"
    ]
    docs = root / "assets/docs"
    topics = (docs / "topics.txt").read_text().splitlines()
    if len(topics) != len(set(topics)) or set(topics) != {
        path.stem for path in docs.glob("*.md")
    }:
        raise ValueError("docs manifest must list every page exactly once")
    bundle = {
        "format": 1,
        "version": version,
        "topics": topics,
        "pages": {topic: (docs / f"{topic}.md").read_text() for topic in topics},
        "llms": (root / "assets/llms.txt").read_text(),
    }
    destination.mkdir(parents=True, exist_ok=True)
    document = destination / "neosian-docs.json"
    document.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n")
    files = [
        root / name
        for name in (
            "LICENSE",
            "llms.txt",
            "docs/README.md",
            "Cargo.toml",
            "pyproject.toml",
            "uv.lock",
            "Makefile",
            "tests/__init__.py",
        )
    ]
    for directory in ("assets", "tests/contract"):
        files.extend(
            path
            for path in (root / directory).rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix != ".pyc"
        )
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for path in sorted(files):
            data = path.read_bytes()
            item = tarfile.TarInfo(path.relative_to(root).as_posix())
            item.size, item.mode = len(data), 0o644
            archive.addfile(item, io.BytesIO(data))
    contracts = destination / "neosian-contracts.tar.gz"
    contracts.write_bytes(gzip.compress(stream.getvalue(), mtime=0))
    artifacts = [document, contracts]
    for path in artifacts:
        path.with_suffix(path.suffix + ".sha256").write_text(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
        )
    return artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    for artifact in build(parser.parse_args().destination):
        print(artifact)


if __name__ == "__main__":
    main()
