"""Shared update policy and PATH findings at the standalone doors."""

import json
import os
from pathlib import Path

import pytest

from tests.differential.harness import Case
from tests.differential.seed import Seeded
from tests.differential.test_setup import compare, spaces


@pytest.mark.parametrize("mode", ("off", "notify", "auto"))
@pytest.mark.parametrize("as_json", (False, True))
def test_update_policy_is_shared(
    seeded: Seeded,
    tmp_path: Path,
    python_door: Path,
    rust_door: Path,
    mode: str,
    as_json: bool,
) -> None:
    pair = spaces(seeded, tmp_path)
    for space in pair:
        (space.home / "config.toml").write_text(
            '[chat]\nmodel = "fake"\n[update]\nother = "kept"\n'
        )
    result, _ = compare(
        Case(
            "update mode", ("update", "--mode", mode, *(("--json",) if as_json else ()))
        ),
        pair,
        python_door,
        rust_door,
    )
    assert result.code == 0


def test_update_refuses_broken_config_without_replacing_it(
    seeded: Seeded,
    tmp_path: Path,
    python_door: Path,
    rust_door: Path,
) -> None:
    pair = spaces(seeded, tmp_path)
    for space in pair:
        (space.home / "config.toml").write_text("[broken")
    result, _ = compare(
        Case("update config", ("update", "--mode", "auto", "--json")),
        pair,
        python_door,
        rust_door,
    )
    assert result.code == 1


def test_path_shadowing_names_the_first_executable_and_ignores_aliases(
    seeded: Seeded,
    tmp_path: Path,
    python_door: Path,
    rust_door: Path,
) -> None:
    pair = spaces(seeded, tmp_path)
    name = "neosian.exe" if os.name == "nt" else "neosian"
    for space in pair:
        for directory in ("binary", "python", "alias"):
            path = space.out / directory / name
            path.parent.mkdir(parents=True)
            if directory == "alias":
                path.symlink_to(space.out / "binary" / name)
            else:
                path.write_text("")
                path.chmod(0o700)
    env = {
        "PATH": os.pathsep.join(
            "{out}/" + d for d in ("binary", "alias", "python", "binary")
        )
    }
    result, _ = compare(
        Case("shadowing", ("status", "--json"), env=env), pair, python_door, rust_door
    )
    payload = json.loads(result.stdout)
    assert len(payload["path_executables"]) == 2
    assert len(payload["shadowing"]) == 1
    assert payload["path_executables"][0] in payload["shadowing"][0]
