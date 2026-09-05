"""The publishing posture (NX, DESIGN §29), pinned keylessly: least
privilege at the top of every workflow, every action by commit SHA with
its version beside it, the base image by its index digest, the one uv pin
in its four sites, Dependabot over all three ecosystems, and the sdist
declaring what never ships. A drift here would otherwise surface only on
the release that carries it."""

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

import neosian

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS = sorted((_ROOT / ".github" / "workflows").glob("*.yml"))
_DOCKERFILE = _ROOT / "Dockerfile"
_PINNED_USE = re.compile(r"^\s*-\s*uses:\s*\S+@[0-9a-f]{40}\s+# v\d+\.\d+\.\d+\s*$")
_ANY_USE = re.compile(r"^\s*-\s*uses:")
_DIGEST = re.compile(r"^FROM python:[^@\s]+@(sha256:[0-9a-f]{64})", re.M)


def _workflow(path: Path) -> dict[Any, Any]:
    # YAML 1.1 reads the `on` key as the boolean True — hence the key type.
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


@pytest.mark.unit
@pytest.mark.parametrize("path", _WORKFLOWS, ids=lambda p: p.name)
def test_every_workflow_starts_least_privileged(path: Path) -> None:
    assert _workflow(path)["permissions"] == {"contents": "read"}


@pytest.mark.unit
@pytest.mark.parametrize("path", _WORKFLOWS, ids=lambda p: p.name)
def test_every_action_is_pinned_by_sha(path: Path) -> None:
    uses = [
        ln for ln in path.read_text(encoding="utf-8").splitlines() if _ANY_USE.match(ln)
    ]
    assert uses, f"{path.name} uses no action"
    unpinned = [ln.strip() for ln in uses if not _PINNED_USE.match(ln)]
    assert not unpinned, unpinned


@pytest.mark.unit
def test_the_base_image_is_pinned_by_one_index_digest() -> None:
    digests = _DIGEST.findall(_DOCKERFILE.read_text(encoding="utf-8"))
    assert len(digests) == 2, "both stages pin the base image"
    assert len(set(digests)) == 1, "the two stages share one digest"


@pytest.mark.unit
def test_the_one_uv_pin_names_the_same_release_everywhere() -> None:
    # Ledger #185 widened at NX: the Dockerfile, the installer and every
    # workflow's UV_VERSION — a bump that misses one site goes red here.
    image = re.search(r"ghcr\.io/astral-sh/uv:(\d+\.\d+\.\d+)", _DOCKERFILE.read_text())
    assert image
    for path in _WORKFLOWS:
        assert _workflow(path)["env"]["UV_VERSION"] == image.group(1), path.name


@pytest.mark.unit
def test_dependabot_keeps_all_three_ecosystems() -> None:
    config = yaml.safe_load(
        (_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    )
    ecosystems = {entry["package-ecosystem"] for entry in config["updates"]}
    assert ecosystems == {"github-actions", "uv", "docker"}


@pytest.mark.unit
def test_the_sdist_leaves_the_planning_surface_out() -> None:
    with (_ROOT / "pyproject.toml").open("rb") as f:
        excluded = tomllib.load(f)["tool"]["hatch"]["build"]["targets"]["sdist"][
            "exclude"
        ]
    assert {"/docs/", "/.claude/", "/.github/", "/.import_linter_cache/"} <= set(
        excluded
    )


_RELEASE = _ROOT / ".github" / "workflows" / "release.yml"


@pytest.mark.unit
def test_the_release_publishes_by_trust_alone() -> None:
    # DESIGN §29: a `v*` tag publishes; a dispatch rehearses; the only
    # credential is the job's OIDC token, exchanged by uv on the `pypi`
    # environment — no long-lived secret is referenced anywhere.
    text = _RELEASE.read_text(encoding="utf-8")
    release = _workflow(_RELEASE)
    triggers = release[True]
    assert triggers["push"]["tags"] == ["v*"]
    assert "workflow_dispatch" in triggers
    assert "secrets." not in text
    pypi = release["jobs"]["pypi"]
    assert pypi["environment"]["name"] == "pypi"
    assert pypi["permissions"]["id-token"] == "write"
    publish = [
        s for s in pypi["steps"] if "--trusted-publishing always" in s.get("run", "")
    ]
    assert len(publish) == 1
    assert publish[0]["if"] == "github.event_name == 'push'"
    others = {name: job for name, job in release["jobs"].items() if name != "pypi"}
    assert not any("id-token" in job.get("permissions", {}) for job in others.values())


@pytest.mark.unit
def test_the_changelog_names_the_version() -> None:
    # Keep a Changelog, kept by the gate (DESIGN §29): `[Unreleased]`
    # accumulates during a phase, the close names the version, and a bump
    # without its section goes red here before `make release` refuses it.
    text = (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "\n## [Unreleased]\n" in text
    assert f"\n## [{neosian.__version__}] - " in text
    assert f"[{neosian.__version__}]: https://github.com/neosae/neosian/" in text
