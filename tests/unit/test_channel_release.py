"""A tagged channel artifact is immutable; package data stays self-contained."""

import base64
import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import check_channel_release as release
from scripts.package_crate import stage

_ROOT = Path(__file__).resolve().parents[2]


def archive(tmp_path: Path, channel: str, version: str = "1.2.3") -> Path:
    path = tmp_path / "artifact.tar.gz"
    if channel == "npm":
        name = "neosian-npm-package/package.json"
        content = json.dumps({"name": "neosian", "version": version})
    else:
        name = f"neosian-{version}/Cargo.toml"
        content = f'[package]\nname = "neosian"\nversion = "{version}"\n'
    data = content.encode()
    with tarfile.open(path, "w:gz") as bundle:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        bundle.addfile(info, io.BytesIO(data))
    return path


@pytest.mark.unit
@pytest.mark.parametrize("channel", ["npm", "crate"])
def test_publication_retries_only_accept_identical_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, channel: str
) -> None:
    path = archive(tmp_path, channel)
    state: dict[str, Any] | None = None
    monkeypatch.setattr(release, "published", lambda *_: state)
    assert release.needs_publish(path, channel, "v1.2.3")
    data = path.read_bytes()
    state = {
        "dist": {
            "integrity": "sha512-"
            + base64.b64encode(hashlib.sha512(data).digest()).decode()
        },
        "version": {"checksum": hashlib.sha256(data).hexdigest()},
    }
    assert not release.needs_publish(path, channel, "v1.2.3")
    path.write_bytes(path.read_bytes() + b"different bytes")
    with pytest.raises(ValueError, match="different bytes"):
        release.needs_publish(path, channel, "v1.2.3")


@pytest.mark.unit
@pytest.mark.parametrize("channel", ["npm", "crate"])
def test_a_wrong_tag_or_prerelease_cannot_reach_the_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, channel: str
) -> None:
    def unexpected(*_args: object) -> None:
        pytest.fail("registry reached before artifact validation")

    monkeypatch.setattr(release, "published", unexpected)
    with pytest.raises(ValueError, match="does not match"):
        release.needs_publish(archive(tmp_path, channel), channel, "v1.2.4")
    with pytest.raises(ValueError, match="stable"):
        release.needs_publish(
            archive(tmp_path, channel, "1.2.3-rc.1"), channel, "v1.2.3rc1"
        )


@pytest.mark.unit
def test_formula_tag_is_checked(tmp_path: Path) -> None:
    path = tmp_path / "neosian.rb"
    path.write_text('class Neosian < Formula\n  version "1.2.3"\nend\n')
    assert release.needs_publish(path, "homebrew", "v1.2.3")
    with pytest.raises(ValueError, match="does not match"):
        release.needs_publish(path, "homebrew", "v1.2.4")


@pytest.mark.unit
def test_staging_replaces_stale_assets_without_changing_the_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "neosian/assets"
    source.mkdir(parents=True)
    (source / "page.md").write_bytes(b"canonical\n")
    (source / "__pycache__").mkdir()
    (source / "__pycache__/cache.pyc").write_bytes(b"not an asset")
    (tmp_path / "LICENSE").write_bytes(b"license")
    crate = tmp_path / "crates/neosian"
    crate.mkdir(parents=True)
    stage(tmp_path)
    (crate / "assets/stale.md").write_bytes(b"old")
    (source / "page.md").write_bytes(b"updated\n")
    stage(tmp_path)
    assert (crate / "assets/page.md").read_bytes() == b"updated\n"
    assert sorted(p.name for p in (crate / "assets").iterdir()) == ["page.md"]
    assert (crate / "LICENSE").read_bytes() == b"license"
    assert (source / "__pycache__/cache.pyc").exists()


@pytest.mark.unit
def test_dispatch_cannot_publish_and_announce_waits_for_the_channels() -> None:
    workflow = yaml.safe_load(
        (_ROOT / ".github/workflows/publish-channels.yml").read_text()
    )
    for job in ("npm", "homebrew"):
        assert "github.event_name == 'push'" in workflow["jobs"][job]["if"]
    crate = workflow["jobs"]["crate"]
    commands = [step.get("run", "") for step in crate["steps"]]
    assert commands.index('test -z "$(git status --porcelain)"') < commands.index(
        "python3 scripts/package_crate.py"
    )
    for command in commands:
        if command.startswith(("cargo package", "cargo publish")):
            assert "--locked --allow-dirty" in command
    check = next(s for s in crate["steps"] if s.get("id") == "check")
    assert "github.event_name == 'push'" in check["if"]
    publish = next(s for s in crate["steps"] if "cargo publish" in s.get("run", ""))
    assert publish["if"] == "steps.check.outputs.publish == 'true'"
    generated = yaml.safe_load((_ROOT / ".github/workflows/release.yml").read_text())
    assert "custom-publish-channels" in generated["jobs"]["announce"]["needs"]
