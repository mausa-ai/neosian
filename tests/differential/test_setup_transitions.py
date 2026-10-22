"""Existing client data, store selection and managed hook transitions."""

import json
import sys
from pathlib import Path

import pytest

from tests.differential.harness import Case
from tests.differential.seed import Seeded
from tests.differential.test_setup import compare, locations, spaces


@pytest.mark.parametrize(
    "client,filename,document",
    [
        (
            "claude-code",
            "settings.json",
            {
                "theme": "kept",
                "hooks": {
                    "Stop": [
                        {"hooks": [{"type": "command", "command": "echo foreign"}]}
                    ]
                },
            },
        ),
        (
            "codex",
            "hooks.json",
            {
                "theme": "kept",
                "hooks": {
                    "Stop": [
                        {"hooks": [{"type": "command", "command": "echo foreign"}]}
                    ]
                },
            },
        ),
        (
            "opencode",
            "opencode.jsonc",
            {
                "theme": "kept",
                "mcp": {"foreign": {"type": "local", "command": ["true"]}},
            },
        ),
        (
            "muse-code",
            "settings.json",
            {
                "schema_version": 1,
                "theme": "kept",
                "hooks": {
                    "Stop": [
                        {"hooks": [{"type": "command", "command": "echo foreign"}]}
                    ]
                },
            },
        ),
        (
            "cursor",
            "hooks.json",
            {
                "version": 1,
                "theme": "kept",
                "hooks": {"stop": [{"command": "echo foreign"}]},
            },
        ),
        (
            "pi",
            "mcp.json",
            {"theme": "kept", "mcpServers": {"foreign": {"command": "true"}}},
        ),
    ],
)
def test_existing_foreign_settings_and_modes_survive(
    seeded: Seeded,
    tmp_path: Path,
    python_door: Path,
    rust_door: Path,
    client: str,
    filename: str,
    document: dict[str, object],
) -> None:
    pair = spaces(seeded, tmp_path)
    for space in pair:
        at = locations(space)
        path = space.out / "client dirs" / client / filename
        path.write_text(json.dumps(document))
        path.chmod(0o640)
    case = Case(
        "preserve",
        (
            "setup",
            "--client",
            client,
            *at,
            "--write",
            "--json",
            "--binary",
            str(rust_door),
        ),
        env={"PATH": ""},
    )
    for _ in range(2):
        result, _ = compare(case, pair, python_door, rust_door)
        assert b'"theme"' not in result.stdout
    for space in pair:
        path = space.out / "client dirs" / client / filename
        assert json.loads(path.read_text())["theme"] == "kept"
        assert path.stat().st_mode & 0o777 == 0o640


@pytest.mark.parametrize(
    "args,env",
    [
        (("--root", "{out}/store"), {}),
        (("--root", ""), {}),
        (("--url", ""), {}),
        (("--url", "bad-url"), {}),
        (("--url", "https://example.test"), {}),
        (("--root", "{home}", "--url", "https://example.test"), {}),
        ((), {"NEOSIAN_SCOPE": "tenant:shared"}),
        ((), {"NEOSIAN_SCOPE": "invalid @ scope"}),
        ((), {"NEOSIAN_POSTGRES_DSN": "postgresql://test-only"}),
        (("--root", "{home}"), {"NEOSIAN_POSTGRES_DSN": "postgresql://test-only"}),
    ],
)
def test_store_selection_and_environment_scope(
    seeded: Seeded,
    tmp_path: Path,
    python_door: Path,
    rust_door: Path,
    args: tuple[str, ...],
    env: dict[str, str],
) -> None:
    pair = spaces(seeded, tmp_path)
    for space in pair:
        at = locations(space)
    compare(
        Case(
            "store",
            (
                "setup",
                "--client",
                "pi",
                *at,
                *args,
                "--json",
                "--binary",
                str(rust_door),
            ),
            env={"PATH": "", **env},
        ),
        pair,
        python_door,
        rust_door,
    )


def test_muse_managed_hooks_return_to_ordinary_without_exposing_values(
    seeded: Seeded,
    tmp_path: Path,
    python_door: Path,
    rust_door: Path,
) -> None:
    pair = spaces(seeded, tmp_path)
    for space in pair:
        at = locations(space)
    common = (
        "setup",
        "--client",
        "muse-code",
        *at,
        "--write",
        "--json",
        "--binary",
        str(rust_door),
    )
    for extra in (
        ("--url", "https://example.test"),
        (),
        ("--url", "https://example.test"),
    ):
        result, _ = compare(
            Case(
                "managed",
                (*common, *extra),
                env={"PATH": "", "NEOSIAN_CLIENT_TOKEN": "test-token"},
            ),
            pair,
            python_door,
            rust_door,
        )
        assert result.code == 0 and b"test-token" not in result.stdout


def test_unicode_paths_and_shell_quotes_in_previews(
    seeded: Seeded,
    tmp_path: Path,
    python_door: Path,
    rust_door: Path,
) -> None:
    pair = spaces(seeded, tmp_path)
    for space in pair:
        (space.out / "client's 雪").mkdir(parents=True)
    for client in ("muse-code", "pi", "claude-code"):
        case = Case(
            "unicode",
            (
                "setup",
                "--client",
                client,
                "--at",
                f"{client}={{out}}/client's 雪",
                "--root",
                "{out}/store's 雪",
                "--binary",
                str(rust_door),
            ),
            env={"PATH": ""},
        )
        compare(case, pair, python_door, rust_door)


def test_empty_path_never_discovers_a_client_executable_in_the_project(
    seeded: Seeded,
    tmp_path: Path,
    python_door: Path,
    rust_door: Path,
) -> None:
    pair = spaces(seeded, tmp_path)
    for space in pair:
        at = locations(space)
        path = space.project / "claude"
        path.write_text(f"#!{sys.executable}\nraise SystemExit(0)\n")
        path.chmod(0o700)
    result, _ = compare(
        Case(
            "empty PATH",
            (
                "setup",
                "--client",
                "claude-code",
                *at,
                "--write",
                "--json",
                "--binary",
                str(rust_door),
            ),
            env={"PATH": ""},
        ),
        pair,
        python_door,
        rust_door,
    )
    assert result.code == 1
    mcp = json.loads(result.stdout)["clients"][0]["mcp"]
    assert mcp["applied"] is False and mcp["apply_error"] is None
