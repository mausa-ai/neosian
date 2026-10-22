"""Standalone installers emit the same artifacts and preserve the same files."""

from pathlib import Path

import pytest

from tests.differential.harness import Case
from tests.differential.seed import Seeded
from tests.differential.test_setup import CLIENTS, client_files, compare, spaces


@pytest.mark.parametrize(
    "verb,client",
    [(v, c) for v in ("mcp", "record") for c in CLIENTS] + [("mcp", "claude-desktop")],
)
@pytest.mark.parametrize("level", ("user", "project"))
@pytest.mark.parametrize("write", (False, True))
@pytest.mark.parametrize("as_json", (False, True))
def test_standalone_installer(
    seeded: Seeded,
    tmp_path: Path,
    python_door: Path,
    rust_door: Path,
    verb: str,
    client: str,
    level: str,
    write: bool,
    as_json: bool,
) -> None:
    pair = spaces(seeded, tmp_path)
    for space in pair:
        for directory in (
            ".claude",
            ".codex",
            ".config/opencode",
            ".config/muse",
            ".cursor",
            ".pi/agent",
            "Library/Application Support/Claude",
            ".config/Claude",
            "AppData/Roaming/Claude",
        ):
            (space.home.parent / directory).mkdir(parents=True, exist_ok=True)
    case = Case(
        "standalone install",
        (
            verb,
            "install",
            "--client",
            client,
            "--level",
            level,
            "--binary",
            str(rust_door),
            *(("--write",) if write else ()),
            *(("--json",) if as_json else ()),
        ),
        env={"PATH": ""},
    )
    result, _ = compare(case, pair, python_door, rust_door)
    expected = 0
    if verb == "mcp":
        if level == "project" and client in ("codex", "cursor", "claude-desktop"):
            expected = 2
        elif write and level == "user" and client in ("claude-code", "codex"):
            expected = 1
    assert result.code == expected
    if not write:
        assert not client_files(pair[0])
    elif result.code == 0:
        compare(case, pair, python_door, rust_door)  # An idempotent re-run.


@pytest.mark.parametrize(
    "verb,extra,env,code",
    [
        ("mcp", ("--actor", "mcp:custom", "--scope", "user:me"), {}, 0),
        ("record", ("--agent", "custom", "--spool", "{out}/a b"), {}, 0),
        ("mcp", ("--mount", "path=kb,ro,scope=user:me"), {}, 0),
        ("record", ("--mount", "scope=user:me,path=kb,eo"), {}, 2),
        ("mcp", ("--scope", "bad"), {}, 2),
        ("mcp", (), {"NEOSIAN_SCOPE": "bad"}, 2),
        ("record", ("--url", ""), {}, 2),
        ("mcp", ("--actor", "bad"), {}, 2),
        ("record", ("--agent", "bad!"), {}, 2),
        ("mcp", ("--schema", "custom"), {}, 2),
        ("mcp", ("--scope", "user:me", "--mount", "scope=user:me,path=kb"), {}, 2),
        (
            "record",
            ("--mount", "scope=user:me,path=kb", "--mount", "scope=user:other,path=kb"),
            {},
            2,
        ),
        ("mcp", ("--url", "https://state.example.test"), {}, 2),
        ("record", ("--url", "bad"), {}, 2),
        (
            "mcp",
            ("--url", "https://state.example.test"),
            {"NEOSIAN_CLIENT_TOKEN": "test-bearer"},
            0,
        ),
        (
            "record",
            ("--url", "https://state.example.test"),
            {"NEOSIAN_CLIENT_TOKEN": "test-bearer"},
            0,
        ),
        (
            "mcp",
            ("--schema", "custom"),
            {"NEOSIAN_POSTGRES_DSN": "postgresql://test"},
            0,
        ),
        (
            "record",
            ("--schema", "custom"),
            {"NEOSIAN_POSTGRES_DSN": "postgresql://test"},
            0,
        ),
    ],
)
def test_settings_and_refusals(
    seeded: Seeded,
    tmp_path: Path,
    python_door: Path,
    rust_door: Path,
    verb: str,
    extra: tuple[str, ...],
    env: dict[str, str],
    code: int,
) -> None:
    pair = spaces(seeded, tmp_path)
    for space in pair:
        (space.home.parent / ".claude").mkdir(exist_ok=True)
    case = Case(
        "standalone settings",
        (
            verb,
            "install",
            "--client",
            "claude-code",
            "--binary",
            str(rust_door),
            "--json",
            *extra,
        ),
        env={"PATH": "", **env},
    )
    result, _ = compare(case, pair, python_door, rust_door)
    assert result.code == code
    assert not client_files(pair[0])
