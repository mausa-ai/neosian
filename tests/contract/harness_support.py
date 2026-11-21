"""A live stdio MCP session and the stand-in harnesses, for the harness tool."""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.contract.support import Binary

STANDIN = Path(__file__).with_name("harness_standin.py")
PROGRAMS = ("claude", "codex", "opencode")


@dataclass(frozen=True)
class Harnesses:
    """A test's stand-in harness executables and their log."""

    binary: Binary
    programs: tuple[str, ...] = PROGRAMS

    @property
    def bin(self) -> Path:
        return self.binary.directory / "standin-bin"

    @property
    def log_dir(self) -> Path:
        return self.binary.directory / "standin-log"

    def install(self) -> Harnesses:
        self.bin.mkdir(exist_ok=True)
        self.log_dir.mkdir(exist_ok=True)
        source = STANDIN.read_text(encoding="utf-8")
        for program in self.programs:
            path = self.bin / program
            path.write_text(f"#!{sys.executable}\n{source}", encoding="utf-8")
            path.chmod(0o755)
        return self

    def env(self, **extra: str) -> dict[str, str]:
        env = self.binary.environment()
        sleep = shutil.which("sleep") or "/bin/sleep"
        env["PATH"] = os.pathsep.join([str(self.bin), str(Path(sleep).parent)])
        env["STANDIN_DIR"] = str(self.log_dir)
        env["STANDIN_NEOSIAN"] = str(self.binary.executable)
        env.update(extra)
        return env

    def enable(self, **settings: int) -> None:
        """`[harness]` enabled in this home and in the default home under
        HOME, which a Codex child's server reads (Codex drops NEOSIAN_HOME)."""
        text = "[harness]\nenabled = true\n" + "".join(
            f"{key} = {value}\n" for key, value in settings.items()
        )
        for home in (self.binary.home, self.binary.directory / ".neosian"):
            home.mkdir(exist_ok=True)
            (home / "config.toml").write_text(text, encoding="utf-8")

    def entries(self) -> list[dict[str, Any]]:
        path = self.log_dir / "log.jsonl"
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    def runs(self) -> list[dict[str, Any]]:
        return [entry for entry in self.entries() if "argv" in entry]


class Session:
    """`neosian mcp` on stdio, request by request."""

    def __init__(self, binary: Binary, env: dict[str, str], cwd: Path | None = None):
        self.process = subprocess.Popen(
            [str(binary.executable), "mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=cwd or binary.directory,
        )
        self.lines: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self.next = 0
        self.request(
            "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}
        )

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.put(line)

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self.next += 1
        message = {"jsonrpc": "2.0", "id": self.next, "method": method}
        if params is not None:
            message["params"] = params
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()
        answer = json.loads(self.lines.get(timeout=60))
        assert answer["id"] == self.next
        return answer

    def tools(self) -> dict[str, Any]:
        listed = self.request("tools/list")["result"]["tools"]
        return {tool["name"]: tool for tool in listed}

    def call(self, name: str, **arguments: Any) -> tuple[bool, Any]:
        """(isError, the answer parsed when it is JSON, else its text)."""
        started = time.monotonic()
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        self.elapsed = time.monotonic() - started
        text = result["result"]["content"][0]["text"]
        try:
            return result["result"]["isError"], json.loads(text)
        except ValueError:
            return result["result"]["isError"], text

    def harness(self, **arguments: Any) -> Any:
        is_error, answer = self.call("harness", **arguments)
        assert not is_error, answer
        return answer

    def close(self, timeout: float = 30) -> str:
        assert self.process.stdin is not None
        self.process.stdin.close()
        self.process.wait(timeout=timeout)
        assert self.process.stderr is not None
        return self.process.stderr.read()


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie still answers; ask ps whether it is one.
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


def eventually(check: Any, seconds: float = 20) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.1)
    return bool(check())
