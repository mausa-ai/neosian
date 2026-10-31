"""Client registration is isolated, idempotent and preserves foreign settings."""

import json
import subprocess

import pytest

from tests.contract.support import Binary

CLIENTS = ("claude-code", "codex", "opencode", "muse-code", "cursor", "pi")


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("level", ["user", "project"])
def test_setup_preview_and_repeat_keep_foreign_files(
    binary: Binary, client: str, level: str
) -> None:
    target = binary.directory / "client configuration"
    target.mkdir()
    sentinel = target / "foreign.txt"
    sentinel.write_text("owned by the client\n")
    args = [
        str(binary.executable),
        "setup",
        "--client",
        client,
        "--level",
        level,
        "--binary",
        str(binary.executable),
        "--at",
        f"{client}={target}",
        "--json",
    ]
    env = {**binary.environment(), "PATH": ""}

    def run(*extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*args, *extra],
            env=env,
            cwd=binary.directory,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    preview = run()
    unsupported = level == "project" and client in {"codex", "cursor"}
    assert preview.returncode == (1 if unsupported else 0), preview.stderr
    assert list(target.iterdir()) == [sentinel]
    first = run("--write")
    expected = (
        1
        # Claude's cache mod is installed through its CLI at either scope.
        if unsupported
        or client == "claude-code"
        or (level == "user" and client == "codex")
        else 0
    )
    assert first.returncode == expected, first.stderr
    files = {
        str(path.relative_to(binary.directory)): path.read_bytes()
        for path in binary.directory.rglob("*")
        if path.is_file() and not path.name.endswith(".lock")
    }
    again = run("--write")
    assert again.returncode == expected
    assert {
        str(path.relative_to(binary.directory)): path.read_bytes()
        for path in binary.directory.rglob("*")
        if path.is_file() and not path.name.endswith(".lock")
    } == files
    assert sentinel.read_text() == "owned by the client\n"
    assert json.loads(first.stdout)  # Every report remains valid structured data.


@pytest.mark.parametrize(
    "client,filename,body",
    [
        ("cursor", "hooks.json", '{"version": 2}'),
        ("muse-code", "settings.json", '{"schema_version": true}'),
        ("pi", "mcp.json", '{"mcpServers": []}'),
    ],
)
def test_setup_preflights_both_halves(
    binary: Binary, client: str, filename: str, body: str
) -> None:
    target = binary.directory / client
    target.mkdir()
    path = target / filename
    path.write_text(body)
    result = binary.run(
        "setup",
        "--client",
        client,
        "--at",
        f"{client}={target}",
        "--binary",
        str(binary.executable),
        "--write",
        "--json",
    )
    assert result.returncode == 1
    assert list(target.iterdir()) == [path]
    assert path.read_text() == body
