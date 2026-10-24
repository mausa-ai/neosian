"""Version and public artifacts no longer depend on a Python distribution."""

import hashlib
import importlib.util
import json
import tarfile
import tomllib
from pathlib import Path

from scripts.package_docs import build
from tests.contract.support import ROOT, package_version


def test_release_artifacts_are_complete_deterministic_and_versioned(
    tmp_path: Path,
) -> None:
    paths = build(tmp_path / "one")
    repeated = build(tmp_path / "two")
    assert [path.read_bytes() for path in paths] == [
        path.read_bytes() for path in repeated
    ]
    docs = json.loads(paths[0].read_text())
    assert docs["format"] == 1 and docs["version"] == package_version()
    assert set(docs["topics"]) == set(docs["pages"])
    assert docs["llms"].encode() == (ROOT / "llms.txt").read_bytes()
    for path in paths:
        assert (
            path.with_suffix(path.suffix + ".sha256").read_text().split()[0]
            == hashlib.sha256(path.read_bytes()).hexdigest()
        )
    with tarfile.open(paths[1]) as archive:
        names = archive.getnames()
        for expected in [
            "assets/contracts/wire-v6.openapi.json",
            "assets/contracts/state-v1.schema.json",
            "assets/contracts/profile-v1.schema.json",
            "assets/contracts/grants-v1.schema.json",
            "assets/contracts/knowledge-v1.schema.json",
            "assets/contracts/skill-v1.schema.json",
            "assets/contracts/upkeep-v1.schema.json",
            "assets/contracts/governance-v1.schema.json",
            "assets/contracts/changes-v1.schema.json",
            "assets/contracts/exchange-v1.schema.json",
            "tests/contract/test_exchange.py",
            "assets/sql/changes-postgres.sql",
            "tests/contract/storage/test_changes.py",
            "assets/sql/postgres.sql",
            "scripts/migrate_v1_metadata.py",
            "tests/contract/storage/test_concurrency.py",
            "LICENSE",
        ]:
            assert expected in names
        assert not any(
            name.startswith(("record/", "neosian/", ".git/")) for name in names
        )


def test_product_has_no_python_distribution_or_model_runtime() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "project" not in data and "build-system" not in data
    assert importlib.util.find_spec("neosian") is None
    for name in ("openai", "anthropic", "textual"):
        assert importlib.util.find_spec(name) is None
    assert (ROOT / "assets/llms.txt").read_bytes() == (ROOT / "llms.txt").read_bytes()
