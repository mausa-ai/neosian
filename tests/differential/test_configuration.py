"""The two doors share config data, sources, failures and client findings."""

import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.differential.harness import Case, Workspace, normalise, run_case
from tests.differential.seed import Seeded
from tests.differential.test_setup import compare, locations, spaces


@pytest.mark.parametrize("as_json", (False, True))
def test_configuration_round_trip_and_permissions(
    seeded: Seeded,
    tmp_path: Path,
    python_door: Path,
    rust_door: Path,
    as_json: bool,
) -> None:
    pair = spaces(seeded, tmp_path)
    for space in pair:
        path = space.home / "config.toml"
        path.write_text(
            '[chat]\nmodel = "fake"\n[[chat.mcp]]\nname = "kept"\ncommand = "echo"\n[update]\nmode = "notify"\n'
        )
        path.chmod(0o644)
        space.home.chmod(0o755)
    tail = ("--json",) if as_json else ()
    steps = [
        ((), None),
        (("--provider", "xai", "--key", "-"), 'test-"key\\\n雪\n'),
        (("--env", "ACME_API_KEY", "--key", "-"), "test-acme\n"),
        (("--list",), None),
        (("--delete", "--provider", "xai"), None),
        (("--delete", "--provider", "xai"), None),
        (("--delete", "--env", "ACME_API_KEY"), None),
        (("--delete",), None),
        (("--delete",), None),
    ]
    for argv, stdin in steps:
        result, _ = compare(
            Case(
                "config",
                ("configure", *argv, *tail),
                stdin=stdin,
                env={"OPENAI_API_KEY": "test-env"},
            ),
            pair,
            python_door,
            rust_door,
        )
        assert result.code == 0
        assert b"test-env" not in result.stdout
        if "--key" in argv and sys.platform != "win32":
            for space in pair:
                assert (
                    stat.S_IMODE((space.home / "config.toml").stat().st_mode) == 0o600
                )
                assert stat.S_IMODE(space.home.stat().st_mode) == 0o700


@pytest.mark.parametrize(
    "args,stdin,code",
    [
        (("--provider", "nope", "--key", "-"), "test", 2),
        (("--provider", "xai", "--key", "literal-test"), "", 2),
        (("--key", "-"), "test", 2),
        (("--provider", "xai"), "", 2),
        (("--env", "lowercase", "--key", "-"), "test", 2),
        (("--env", "ACME_API_KEY", "--provider", "xai", "--key", "-"), "test", 2),
        (("--env", "XAI_API_KEY", "--key", "-"), " \n", 1),
    ],
)
def test_configuration_refusals(
    seeded: Seeded,
    tmp_path: Path,
    python_door: Path,
    rust_door: Path,
    args: tuple[str, ...],
    stdin: str,
    code: int,
) -> None:
    pair = spaces(seeded, tmp_path)
    result, _ = compare(
        Case("config refusal", ("configure", *args, "--json"), stdin=stdin),
        pair,
        python_door,
        rust_door,
    )
    assert result.code == code
    assert not (pair[0].home / "config.toml").exists()


def status(case: Case, space: Workspace, executable: Path) -> Any:
    result = run_case(executable, case, space)
    assert result.code == 0 and result.stderr == b""
    return json.loads(normalise(result.stdout.decode(), space, keep=frozenset()))


@pytest.mark.parametrize(
    "config",
    (
        "",
        "[broken",
        '[credentials]\nxai_api_key = "test-key"\n[update]\nmode = "notify"\n',
    ),
)
@pytest.mark.parametrize("installed", (False, True))
def test_status_names_the_shape_and_shares_every_other_finding(
    seeded: Seeded,
    tmp_path: Path,
    python_door: Path,
    rust_door: Path,
    config: str,
    installed: bool,
) -> None:
    pair = spaces(seeded, tmp_path)
    for space in pair:
        at = locations(space)
        if config:
            (space.home / "config.toml").write_text(config)
        if installed:
            run_case(
                python_door,
                Case(
                    "setup",
                    ("setup", "--write", "--yes", "--binary", str(rust_door), *at),
                    env={"PATH": ""},
                ),
                space,
            )
    case = Case("status", ("status", *at, "--json"))
    py, rust = (
        status(case, space, exe)
        for space, exe in zip(pair, (python_door, rust_door), strict=True)
    )
    assert rust.pop("shape") == "binary"
    assert rust.pop("upgrade") == "neosian-update"
    assert py.pop("shape") == "project"
    assert py.pop("upgrade") == "uv add 'neosian==<version>'"
    assert py == rust
    assert py["last_session"] is not None


def test_broken_config_is_refused_and_delete_recovers(
    seeded: Seeded,
    tmp_path: Path,
    python_door: Path,
    rust_door: Path,
) -> None:
    pair = spaces(seeded, tmp_path)
    for space in pair:
        (space.home / "config.toml").write_text("[broken")
    result, _ = compare(
        Case("broken", ("configure", "--list", "--json")), pair, python_door, rust_door
    )
    assert result.code == 1
    result, _ = compare(
        Case("recover", ("configure", "--delete", "--json")),
        pair,
        python_door,
        rust_door,
    )
    assert result.code == 0


def test_status_never_creates_a_missing_home(
    seeded: Seeded,
    tmp_path: Path,
    python_door: Path,
    rust_door: Path,
) -> None:
    pair = spaces(seeded, tmp_path)
    case = Case(
        "empty status", ("status", "--json"), env={"NEOSIAN_HOME": "{out}/missing"}
    )
    for space, executable in zip(pair, (python_door, rust_door), strict=True):
        report = status(case, space, executable)
        assert report["home_exists"] is False
        assert report["config_exists"] is False
        assert report["last_session"] is None
        assert not space.out.exists()


def test_status_plain_text_matches_except_for_its_own_installation(
    seeded: Seeded,
    tmp_path: Path,
    python_door: Path,
    rust_door: Path,
) -> None:
    pair = spaces(seeded, tmp_path)
    lines = []
    for space, executable in zip(pair, (python_door, rust_door), strict=True):
        result = run_case(executable, Case("text", ("status",)), space)
        assert result.code == 0 and result.stderr == b""
        lines.append(
            normalise(result.stdout.decode(), space, keep=frozenset()).splitlines()
        )
    assert "  binary  upgrade: neosian-update" in lines[1][0]
    assert "  project  upgrade: uv add 'neosian==<version>'" in lines[0][0]
    assert lines[0][1:] == lines[1][1:]
