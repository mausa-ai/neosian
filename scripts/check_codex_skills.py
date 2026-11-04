"""Exercise setup and native skill discovery with the official Codex CLI, keyless."""

from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


class Discovery:
    def __init__(self, codex: str, cwd: Path, env: dict[str, str]) -> None:
        self.cwd = cwd
        self.output: queue.Queue[str | None] = queue.Queue()
        self.process = subprocess.Popen(
            [codex, "app-server", "--stdio"],
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()
        self.number = 0

    def read(self) -> None:
        assert self.process.stdout
        for line in self.process.stdout:
            self.output.put(line)
        self.output.put(None)

    def send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def request(self, method: str, params: dict[str, Any]) -> Any:
        self.number += 1
        self.send({"id": self.number, "method": method, "params": params})
        deadline = time.monotonic() + 30
        while True:
            line = self.output.get(timeout=max(0.01, deadline - time.monotonic()))
            if line is None:
                raise AssertionError("Codex exited before replying; inspect its stderr")
            message = json.loads(line)
            if message.get("id") == self.number:
                assert "error" not in message, message
                return message["result"]
            assert time.monotonic() < deadline, "Codex response timed out"

    def skills(self) -> list[Any]:
        result = self.request(
            "skills/list", {"cwds": [str(self.cwd)], "forceReload": True}
        )
        return [
            skill
            for entry in result["data"]
            for skill in entry["skills"]
            if skill["name"] == "neosian-upkeep"
        ]

    def close(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.reader.join(timeout=5)
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                stream.close()


def setup(binary: Path, cwd: Path, env: dict[str, str], *args: str) -> Any:
    result = subprocess.run(
        [str(binary), "setup", "--client", "codex", *args, "--json"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def mcp_workflow(codex_home: Path, cwd: Path, env: dict[str, str]) -> None:
    config = tomllib.loads((codex_home / "config.toml").read_text())
    entry = config["mcp_servers"]["neosian-memory"]
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "codex-skills-check", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "load_skill", "arguments": {"name": "builtin:upkeep"}},
        },
    ]
    result = subprocess.run(
        [entry["command"], *entry["args"]],
        cwd=cwd,
        env=env,
        input="".join(json.dumps(r) + "\n" for r in requests),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    replies = {
        r["id"]: r
        for line in result.stdout.splitlines()
        if "id" in (r := json.loads(line))
    }
    tools = {tool["name"] for tool in replies[2]["result"]["tools"]}
    assert {"load_skill", "docs", "upkeep_review", "upkeep_apply"} <= tools
    loaded = replies[3]["result"]
    assert loaded.get("isError") is not True, loaded
    body = "\n".join(c.get("text", "") for c in loaded["content"])
    assert (
        "upkeep_review" in body and "upkeep_apply" in body and "paused" in body.lower()
    ), loaded


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/neosian")
    parser.add_argument("--codex", default=shutil.which("codex"))
    args = parser.parse_args()
    assert args.codex, "Install the official @openai/codex CLI before this check"
    binary = args.binary.resolve()
    assert binary.is_file(), binary
    with tempfile.TemporaryDirectory(prefix="neosian-codex-") as temporary:
        home = Path(temporary).resolve()
        codex_home = home / "codex home"
        codex_home.mkdir()
        cwd = home / "workspace"
        cwd.mkdir()
        env = {
            key: value
            for key, value in os.environ.items()
            if key
            in {
                "PATH",
                "SYSTEMROOT",
                "WINDIR",
                "COMSPEC",
                "PATHEXT",
                "TMP",
                "TEMP",
                "TMPDIR",
            }
        }
        env.update(
            {
                "HOME": str(home),
                "USERPROFILE": str(home),
                "CODEX_HOME": str(codex_home),
                "NEOSIAN_HOME": str(home / "memory"),
                "LOGNAME": "codex-skill-check",
            }
        )
        env["PATH"] = (
            str(Path(args.codex).absolute().parent) + os.pathsep + env.get("PATH", "")
        )
        version = subprocess.check_output(
            [args.codex, "--version"], env=env, text=True
        ).strip()
        preview = setup(binary, cwd, env)
        assert preview["clients"][0]["skills"]["action"] == "install"
        assert not (codex_home / "skills").exists()
        setup(binary, cwd, env, "--write")
        discovery = Discovery(args.codex, cwd, env)
        try:
            discovery.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "neosian_skill_discovery_check",
                        "version": "1.0.0",
                    }
                },
            )
            discovery.send({"method": "initialized", "params": {}})
            found = discovery.skills()
            assert len(found) == 1 and found[0]["enabled"] is True, found
            assert Path(found[0]["path"]).samefile(
                codex_home / "skills/neosian-upkeep/SKILL.md"
            )
            assert setup(binary, cwd, env, "--check")["success"] is True
            mcp_workflow(codex_home, cwd, env)
            setup(binary, cwd, env, "--write", "--codex-skills", "off")
            assert discovery.skills() == []
            setup(binary, cwd, env, "--write")
            assert discovery.skills() == []
            setup(binary, cwd, env, "--write", "--codex-skills", "on")
            assert len(discovery.skills()) == 1
            for command in [
                ["profile", "init", "review", "--write"],
                [
                    "profile",
                    "bind",
                    "review",
                    "--source",
                    "workspace",
                    "--root",
                    str(home / "profile-memory"),
                    "--write",
                ],
            ]:
                result = subprocess.run(
                    [str(binary), *command],
                    cwd=cwd,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                assert result.returncode == 0, result.stdout + result.stderr
            setup(binary, cwd, env, "--write", "--profile", "review")
            mcp_workflow(codex_home, cwd, env)
        finally:
            discovery.close()
        print(
            f"{version}: native upkeep discovery, live MCP instructions, removal and re-enable passed"
        )


if __name__ == "__main__":
    main()
