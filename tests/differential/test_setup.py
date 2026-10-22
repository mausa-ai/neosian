"""R3's six registrations: artifacts, refusals, transitions and client writers.

Both processes register the same binary, so argv compare exactly. Client
directories and PATH live inside each private workspace; no installed client
is invoked. JSON files compare parsed, plugins and shell commands byte-equal.
"""

from __future__ import annotations

import json
import stat
import sys
import tomllib
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest

from tests.differential.harness import (
    Case,
    Outcome,
    Workspace,
    normalise,
    run_case,
    shape_of_output,
    snapshot,
    workspace,
)
from tests.differential.seed import PROJECT_NAME, Seeded

CLIENTS = ("claude-code", "codex", "opencode", "muse-code", "cursor", "pi")


def spaces(seeded: Seeded, tmp_path: Path) -> tuple[Workspace, Workspace]:
    def copy(door: str) -> Workspace:
        return workspace(
            seeded.home,
            tmp_path / door,
            user=seeded.user,
            proj=seeded.proj,
            session=seeded.sessions[-1],
            project_name=PROJECT_NAME,
        )

    return copy("python"), copy("rust")


def locations(space: Workspace, *, present: bool = True) -> tuple[str, ...]:
    args: list[str] = []
    for client in CLIENTS:
        path = space.out / "client dirs" / client
        if present:
            path.mkdir(parents=True, exist_ok=True)
        args += ["--at", f"{client}={{out}}/client dirs/{client}"]
    return tuple(args)


def client_files(space: Workspace) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for path in sorted(space.home.parent.rglob("*")):
        if not path.is_file() or space.home in path.parents:
            continue
        content = normalise(path.read_text(), space, keep=frozenset())
        value: Any = content
        if path.suffix in (".json", ".jsonc"):
            with suppress(ValueError):
                value = json.loads(
                    normalise(json.dumps(json.loads(content)), space, keep=frozenset())
                )
        if path.suffix == ".toml":
            value = tomllib.loads(content)
        files[normalise(str(path), space, keep=frozenset())] = value
    return files


def compare(
    case: Case,
    pair: tuple[Workspace, Workspace],
    python: Path,
    rust: Path,
) -> tuple[Outcome, Outcome]:
    results = [
        run_case(executable, case, space)
        for executable, space in zip((python, rust), pair, strict=True)
    ]
    shaped = [
        shape_of_output(case, result, space, keep=frozenset())
        for result, space in zip(results, pair, strict=True)
    ]
    assert shaped[0] == shaped[1]
    assert client_files(pair[0]) == client_files(pair[1])
    assert snapshot(pair[0], keep=frozenset()) == snapshot(pair[1], keep=frozenset())
    return results[0], results[1]


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("level", ("user", "project"))
@pytest.mark.parametrize("write", (False, True))
@pytest.mark.parametrize("as_json", (False, True))
def test_every_client_and_level(
    seeded: Seeded,
    tmp_path: Path,
    python_door: Path,
    rust_door: Path,
    client: str,
    level: str,
    write: bool,
    as_json: bool,
) -> None:
    pair = spaces(seeded, tmp_path)
    for space in pair:
        at = locations(space)
    case = Case(
        "setup",
        (
            "setup",
            "--client",
            client,
            "--level",
            level,
            "--binary",
            str(rust_door),
            *at,
            *(("--write",) if write else ()),
            *(("--json",) if as_json else ()),
        ),
        env={"PATH": ""},
    )
    result, _ = compare(case, pair, python_door, rust_door)
    expected = 1 if level == "project" and client in ("codex", "cursor") else 0
    if write and client in ("codex", "claude-code") and level == "user":
        expected = 1  # CLI-owned MCP pending; the hooks still land.
    assert result.code == expected
    if not write:
        assert not client_files(pair[0])


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("remote", (False, True))
def test_rerun_and_project_to_user_transition(
    seeded: Seeded,
    tmp_path: Path,
    python_door: Path,
    rust_door: Path,
    client: str,
    remote: bool,
) -> None:
    pair = spaces(seeded, tmp_path)
    for space in pair:
        at = locations(space)
    common = (
        "setup",
        "--client",
        client,
        "--binary",
        str(rust_door),
        *at,
        "--write",
        "--json",
    )
    env = {"PATH": "", "NEOSIAN_CLIENT_TOKEN": "test-bearer"}
    if remote:
        common += ("--url", "https://state.example.test")
    for level in ("project", "user", "user"):
        compare(
            Case("transition", (*common, "--level", level), env=env),
            pair,
            python_door,
            rust_door,
        )


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("--write",),
        ("--write", "--yes"),
        ("--client", "pi"),
        ("--at", "nope=x"),
        ("--at", "pi="),
        ("--at", "pi=a", "--at", "pi=b"),
        ("--binary", "/missing/neosian"),
    ],
)
def test_refusals_leave_client_files_untouched(
    seeded: Seeded,
    tmp_path: Path,
    python_door: Path,
    rust_door: Path,
    args: tuple[str, ...],
) -> None:
    pair = spaces(seeded, tmp_path)
    compare(
        Case("refusal", ("setup", *args, "--json"), env={"PATH": ""}),
        pair,
        python_door,
        rust_door,
    )
    assert not client_files(pair[0])


@pytest.mark.parametrize(
    "client,filename,content",
    [
        ("cursor", "hooks.json", '{"version": 2}'),
        ("cursor", "hooks.json", '{"version": 1, "hooks": {"unrelated": [42]}}'),
        ("muse-code", "settings.json", '{"schema_version": true}'),
        ("muse-code", "settings.json", '{"schema_version": 1, "hooks": []}'),
        ("muse-code", "settings.json", '{"schema_version": 1, "mcp_servers": {}}'),
        ("pi", "mcp.json", '{"mcpServers": []}'),
        ("pi", "mcp.json", "not json"),
    ],
)
def test_preflight_refuses_both_halves_without_writing(
    seeded: Seeded,
    tmp_path: Path,
    python_door: Path,
    rust_door: Path,
    client: str,
    filename: str,
    content: str,
) -> None:
    pair = spaces(seeded, tmp_path)
    for space in pair:
        at = locations(space)
        (space.out / "client dirs" / client / filename).write_text(content)
    before = client_files(pair[0])
    result, _ = compare(
        Case(
            "preflight",
            (
                "setup",
                "--client",
                client,
                *at,
                "--binary",
                str(rust_door),
                "--write",
                "--json",
            ),
            env={"PATH": ""},
        ),
        pair,
        python_door,
        rust_door,
    )
    assert result.code == 1
    assert client_files(pair[0]) == before


def test_client_owned_writers_apply_retry_and_keep_other_settings(
    seeded: Seeded,
    tmp_path: Path,
    python_door: Path,
    rust_door: Path,
) -> None:
    pair = spaces(seeded, tmp_path)
    script = """import json, os, pathlib, sys
client = pathlib.Path(sys.argv[0]).name
base = pathlib.Path(os.environ['CODEX_HOME' if client == 'codex' else 'CLAUDE_CONFIG_DIR'])
path = base / ('config.toml' if client == 'codex' else '.claude.json')
args = sys.argv[1:]
if args[1] == 'remove':
    (base / 'removed').touch()
    sys.exit(0)
if path.exists() and not (base / 'removed').exists():
    sys.exit(1)
if client == 'codex':
    parts = args[args.index('--')+1:]
    path.write_text('theme = "kept"\\n[mcp_servers.neosian-memory]\\ncommand = ' + json.dumps(parts[0]) + '\\nargs = ' + json.dumps(parts[1:]) + '\\n')
else:
    path.write_text(json.dumps({'other': 'kept', 'mcpServers': {'neosian-memory': json.loads(args[-1])}}))
"""
    for space in pair:
        at = locations(space)
        binary = space.out / "bin"
        binary.mkdir()
        for name in ("claude", "codex"):
            path = binary / name
            path.write_text(f"#!{sys.executable}\n" + script)
            path.chmod(0o700)
    case = Case(
        "CLI writers",
        ("setup", "--write", "--yes", "--json", *at, "--binary", str(rust_door)),
        env={"PATH": "{out}/bin"},
    )
    for _ in range(2):
        result, _ = compare(case, pair, python_door, rust_door)
        assert result.code == 0
    for space in pair:
        assert (space.out / "client dirs/codex/removed").exists()
        assert (space.out / "client dirs/claude-code/removed").exists()
        if sys.platform != "win32":
            for path in (space.out / "client dirs").rglob("*.json"):
                if path.name == ".claude.json":  # The fake client owns this file.
                    continue
                assert stat.S_IMODE(path.stat().st_mode) == 0o600
