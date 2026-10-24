"""Every mailbox transition through the CLI, with each storage transport."""

from __future__ import annotations

import json
import os
import subprocess
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.differential.harness import (
    Case,
    Workspace,
    run_case,
    shape_of_output,
    snapshot,
)
from tests.differential.remote_cases import remote_case, serving
from tests.differential.seed import Seeded
from tests.differential.test_remote import _space


@pytest.mark.parametrize("server_kind", ["local", "python", "rust"])
def test_lifecycle_and_fences_match_through_each_server(
    server_kind: str,
    seeded: Seeded,
    python_door: Path,
    rust_door: Path,
    tmp_path: Path,
) -> None:
    observed: list[Any] = []
    server = {"local": None, "python": python_door, "rust": rust_door}[server_kind]
    for client in (python_door, rust_door):
        space = _space(seeded, tmp_path / str(len(observed)))
        reports: list[Any] = []
        with serving(server, space) if server else nullcontext(None) as url:

            def invoke(
                command: str,
                *args: str,
                code: int = 0,
                client: Path = client,
                space: Workspace = space,
                reports: list[Any] = reports,
                url: str | None = url,
            ) -> dict[str, Any]:
                case = Case(
                    f"messages {command}",
                    (
                        "messages",
                        command,
                        *args,
                        "--root",
                        "{home}",
                        "--mount",
                        "scope={proj},path=project",
                        "--session",
                        "mailbox-worker",
                        "--json",
                    ),
                )
                if url:
                    case = remote_case(case, url)
                result = run_case(client, case, space)
                assert result.code == code, (result.stdout, result.stderr)
                reports.append(shape_of_output(case, result, space, keep=seeded.ids))
                envelope = json.loads(result.stdout)
                answer: dict[str, Any] = (
                    json.loads(envelope["data"]) if code == 0 else envelope
                )
                return answer

            item = invoke("send", "--body", "finish the review", "--actionable")
            address = ("--message-id", item["id"], "--occurrence", "1")
            invoke("view", "--message-id", item["id"])
            claimed = invoke("claim", *address)
            token = ("--token", claimed["claim_token"])
            invoke("renew", *address, *token)
            invoke(
                "complete", *address, "--token", "stale", "--outcome", "done", code=1
            )
            invoke("release", *address, *token)
            claimed = invoke("claim", *address)
            token = ("--token", claimed["claim_token"])
            completed = invoke("complete", *address, *token, "--outcome", "reviewed")
            again = invoke("complete", *address, *token, "--outcome", "reviewed")
            assert completed == again
            assert completed["status"] == "done"

            item = invoke("send", "--body", "check later", "--actionable")
            address = ("--message-id", item["id"], "--occurrence", "1")
            claimed = invoke("claim", *address)
            snoozed = invoke(
                "snooze",
                *address,
                "--token",
                claimed["claim_token"],
                "--delay-seconds",
                "600",
                "--outcome",
                "still waiting",
            )
            assert snoozed["occurrence"] == 2 and snoozed["claim_token"] is None
            invoke("cancel", *address, code=1)
            invoke("cancel", "--message-id", item["id"], "--occurrence", "2")

            item = invoke("send", "--body", "information")
            address = ("--message-id", item["id"], "--occurrence", "1")
            acknowledged = invoke("ack", *address)
            assert invoke("ack", *address) == acknowledged
            assert acknowledged["read_by"] == "mailbox-worker"
            view = invoke("view", "--message-id", item["id"])
            assert len(view["history"]) == 2
            invoke("list", "--status", "all")
        observed.append((reports, snapshot(space, keep=seeded.ids)))
    assert observed[0] == observed[1]


@pytest.mark.parametrize(
    "argv",
    [
        ("chat", "prompt", "--model", "fake"),
        ("playground", "absent.py"),
        ("eval", "run", "absent.py"),
        ("memory", "maintain", "--scope", "user:test"),
        ("reflect",),
    ],
)
@pytest.mark.parametrize("flags", [(), ("--json",), ("--help",)])
def test_model_commands_name_python_without_constructing_a_home(
    argv: tuple[str, ...],
    flags: tuple[str, ...],
    seeded: Seeded,
    rust_door: Path,
    tmp_path: Path,
) -> None:
    space = _space(seeded, tmp_path / "model")
    space = replace(space, home=tmp_path / "unused-home")
    case = Case("Python command guidance", (*argv, *flags))
    result = run_case(rust_door, case, space)
    assert result.code == 1
    assert result.stderr == b"needs the Python shell: `uv tool install neosian`\n"
    if "--json" in flags:
        assert json.loads(result.stdout)["error"] == result.stderr.decode().strip()
    else:
        assert result.stdout == b""
    assert not space.home.exists()


def test_bare_binary_under_a_pipe_prints_help_without_opening_the_home(
    seeded: Seeded, rust_door: Path, tmp_path: Path
) -> None:
    space = replace(_space(seeded, tmp_path / "bare"), home=tmp_path / "unused-home")
    result = subprocess.run(
        [str(rust_door)],
        env=space.environment(),
        cwd=space.project,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert b"Usage:" in result.stdout and not result.stderr
    assert not space.home.exists()


@pytest.mark.parametrize("argv", [(), ("chat", "--json")])
def test_help_and_python_guidance_treat_a_closed_pipe_as_the_readers_choice(
    argv: tuple[str, ...], seeded: Seeded, rust_door: Path, tmp_path: Path
) -> None:
    space = replace(_space(seeded, tmp_path / "pipe"), home=tmp_path / "unused-home")
    reader, writer = os.pipe()
    os.close(reader)
    try:
        result = subprocess.run(
            [str(rust_door), *argv],
            env=space.environment(),
            cwd=space.project,
            stdin=subprocess.DEVNULL,
            stdout=writer,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    finally:
        os.close(writer)
    assert result.returncode == 0 and not result.stderr
    assert not space.home.exists()
