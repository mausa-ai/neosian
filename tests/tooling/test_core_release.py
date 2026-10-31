"""A publication repair cannot substitute product code for an existing tag."""

from pathlib import Path

import pytest

from scripts.core_release import git, plan


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "release-test@example.invalid")
    git(tmp_path, "config", "user.name", "Release test")
    (tmp_path / "Cargo.toml").write_text('[workspace.package]\nversion = "1.2.3"\n')
    commit(tmp_path, "seed")
    git(tmp_path, "tag", "v1.2.3")
    return tmp_path


def commit(root: Path, message: str) -> None:
    git(root, "add", ".")
    git(root, "-c", "commit.gpgsign=false", "commit", "-qm", message)


def test_recovery_checks_tests_but_builds_the_immutable_tag(repository: Path) -> None:
    release = git(repository, "rev-parse", "HEAD")
    path = repository / "tests/contract/replay/test_reference.py"
    path.parent.mkdir(parents=True)
    path.write_text("# repair the fixture's freshness condition\n")
    commit(repository, "repair")
    assert git(repository, "rev-parse", "HEAD") != release
    assert plan(repository, "v1.2.3") == {
        "version": "1.2.3",
        "revision": release,
        "tag": "v1.2.3",
        "publish": "true",
    }
    assert plan(repository, "")["publish"] == "false"


@pytest.mark.parametrize(
    "name",
    ["crates/neosian/src/main.rs", "assets/docs/cli.md", "Cargo.lock", "Dockerfile"],
)
def test_recovery_refuses_changed_product_and_build_inputs(
    repository: Path, name: str
) -> None:
    path = repository / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("changed\n")
    commit(repository, "different product")
    with pytest.raises(ValueError, match="product differs"):
        plan(repository, "v1.2.3")


@pytest.mark.parametrize("tag", ["v1.2.4", "--all", "v1.2.3^{commit}"])
def test_recovery_refuses_wrong_or_malformed_tag(repository: Path, tag: str) -> None:
    with pytest.raises(ValueError, match="release tag must"):
        plan(repository, tag)


def test_recovery_refuses_uncommitted_product_changes(repository: Path) -> None:
    (repository / "Cargo.toml").write_text(
        '[workspace.package]\nversion = "1.2.3"\n# dirty\n'
    )
    with pytest.raises(ValueError, match="clean checkout"):
        plan(repository, "v1.2.3")
