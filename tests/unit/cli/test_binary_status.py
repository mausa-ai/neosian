"""A second executable is a finding; a second symlink to one executable is not."""

import json
import os
from pathlib import Path

import pytest

from neosian._cli.shape import path_executables, shadowing
from neosian._cli.status import client_status, collect, render_text
from neosian._foundation.shared.client_config import Environment


def test_path_order_deduplicates_aliases_and_skips_nonexecutables(
    tmp_path: Path,
) -> None:
    name = "neosian.exe" if os.name == "nt" else "neosian"
    for directory in ("first", "alias", "second", "ignored"):
        (tmp_path / directory).mkdir()
    first, second = (tmp_path / d / name for d in ("first", "second"))
    for path in (first, second):
        path.write_text("")
        path.chmod(0o700)
    (tmp_path / "alias" / name).symlink_to(first)
    if os.name != "nt":
        (tmp_path / "ignored" / name).write_text("")
        (tmp_path / "ignored" / name).chmod(0o600)
    env = {
        "PATH": os.pathsep.join(
            str(tmp_path / d) for d in ("first", "alias", "ignored", "second", "first")
        )
    }
    paths = path_executables(env, tmp_path)
    assert paths == (str(first), str(second))
    assert shadowing(paths) == (
        f"PATH selects {first}; also found {second}. Run an absolute path to choose an installation",
    )
    assert not shadowing(paths[:1])
    assert not path_executables({"PATH": ""}, tmp_path)


@pytest.mark.parametrize("module", (False, True))
async def test_status_names_the_registered_executable_kind(
    tmp_path: Path, module: bool
) -> None:
    client = tmp_path / ".claude"
    client.mkdir()
    binary = tmp_path / "neosian"
    binary.write_text("")
    args = ["-m", "neosian.mcp"] if module else ["mcp"]
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"neosian-memory": {"command": str(binary), "args": args}}}
        )
    )
    context = Environment(
        home=tmp_path, cwd=tmp_path, platform="darwin", env={}, executable=str(binary)
    )
    kind = "interpreter" if module else "binary"
    assert client_status("claude-code", context).executable_kind == kind
    report = await collect(context, {"NEOSIAN_HOME": str(tmp_path / "home")})
    assert f"{kind} ok" in render_text(report)
    binary.unlink()
    report = await collect(context, {"NEOSIAN_HOME": str(tmp_path / "home")})
    assert f"{kind} missing:" in render_text(report)
