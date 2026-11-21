"""A machine with four registered clients, driven through CLI-only doubles."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.contract.support import Binary

CLAUDE = """\
import json, os, pathlib, sys
args = sys.argv[1:]
base = pathlib.Path(os.environ["CLAUDE_CONFIG_DIR"])
with (base / "commands.jsonl").open("a") as log:
    log.write(json.dumps(args) + "\\n")
def load(path):
    # Claude Code 2.1.330 skips a byte-order mark.
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
def touch():
    path = pathlib.Path(os.environ.get("CLAUDE_TOUCH_FILE", base / "settings.json"))
    document = load(path)
    document["touchedByClaude"] = True
    path.write_text(json.dumps(document))
command = " ".join(args)
fails = [f for f in os.environ.get("CLAUDE_FAIL", "").split("|") if f]
fail = next((f for f in fails if command.startswith(f)), None)
if fail:
    if os.environ.get("CLAUDE_TOUCH"):
        # The client writes its own settings before failing.
        touch()
    sys.exit("claude double refused " + fail)
# The members Claude Code 2.1.330's add-json kept; it dropped every other one.
KEPT = os.environ.get("CLAUDE_KEEPS", "type command args env timeout").split()
def project():
    # Local scope is keyed by the repository root, else the directory itself.
    here = pathlib.Path(os.getcwd()).resolve()
    root = next((d for d in [here, *here.parents] if (d / ".git").exists()), here)
    with (base / "local.jsonl").open("a") as log:
        log.write(json.dumps({"args": args, "cwd": str(here)}) + "\\n")
    return str(root)
def servers(document, scope):
    if scope == "local":
        return document.setdefault("projects", {}).setdefault(project(), {}).setdefault("mcpServers", {})
    return document.setdefault("mcpServers", {})
if args[:2] == ["mcp", "add-json"]:
    path = base / ".claude.json"
    document = load(path)
    found = servers(document, args[3])
    if args[4] in found:
        sys.exit("MCP server " + args[4] + " already exists in " + args[3] + " config")
    if args[5] == "-":
        entry = json.loads(sys.stdin.read())
    else:
        entry = {"type": "stdio", **json.loads(args[5])}
    # Members a CLI adds on its own (CLAUDE_ADDS, a JSON object).
    entry = {**json.loads(os.environ.get("CLAUDE_ADDS", "{}")), **entry}
    found[args[4]] = {key: value for key, value in entry.items() if key in KEPT}
    path.write_text(json.dumps(document, indent=1))
elif args[:2] == ["mcp", "remove"]:
    path = base / ".claude.json"
    document = load(path)
    found = servers(document, args[3] if args[2] == "--scope" else "user")
    if args[-1] not in found:
        sys.exit("No MCP server named " + args[-1])
    # A CLI that says it removed an entry it kept.
    if args[-1] != os.environ.get("CLAUDE_KEEPS_REMOVED"):
        del found[args[-1]]
    path.write_text(json.dumps(document, indent=1))
elif args[:2] == ["plugin", "install"]:
    path = base / "settings.json"
    document = load(path)
    document.setdefault("enabledPlugins", {})[args[2]] = True
    path.write_text(json.dumps(document))
touching = os.environ.get("CLAUDE_TOUCH_ON")
if touching and command.startswith(touching):
    # The client writes its own settings while a command succeeds.
    touch()
sleeping = os.environ.get("CLAUDE_SLEEP_ON")
if sleeping and command.startswith(sleeping):
    # A refresh killed while the client works: the test kills it now.
    (base / "sleeping").write_text(str(os.getpid()))
    import time
    time.sleep(60)
"""

CODEX = """\
import json, os, pathlib, sys
base = pathlib.Path(os.environ["CODEX_HOME"])
with (base / "commands.jsonl").open("a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
assert sys.argv[1:3] == ["mcp", "add"] and sys.argv[4] == "--"
assert sys.argv[3] in ("neosian", "neosian-memory")
(base / "config.toml").write_text(
    "[mcp_servers." + sys.argv[3] + "]\\ncommand = " + json.dumps(sys.argv[5])
    + "\\nargs = " + json.dumps(sys.argv[6:]) + "\\nstartup_timeout_sec = 20\\n"
)
"""

CLIENTS = ("claude-code", "codex", "opencode", "muse-code", "cursor", "pi")
HOMES = {
    "claude-code": "clients/claude-code",
    "codex": "clients/codex",
    "opencode": "clients/opencode",
    "muse-code": "xdg/muse",
    "cursor": ".cursor",
    "pi": "clients/pi",
}


def tree(directory: Path) -> dict[str, tuple[str, int, bytes]]:
    """Every entry with its kind and mode: links stay links, modes are visible."""
    found = {}
    for parent, directories, names in os.walk(directory):
        for name in [*directories, *names]:
            path = Path(parent) / name
            status = path.lstat()
            mode = stat.S_IMODE(status.st_mode)
            if stat.S_ISLNK(status.st_mode):
                entry = ("link", 0, os.readlink(path).encode())
            elif stat.S_ISDIR(status.st_mode):
                entry = ("directory", mode, b"")
            else:
                entry = ("file", mode, path.read_bytes())
            found[str(path.relative_to(directory))] = entry
    return found


@dataclass(frozen=True)
class Machine:
    binary: Binary
    project: Path

    @property
    def root(self) -> Path:
        return self.binary.directory

    def home(self, client: str) -> Path:
        return self.root / HOMES[client]

    def environment(self, **extra: str) -> dict[str, str]:
        return {
            **self.binary.environment(),
            "PATH": str(self.root / "commands"),
            "CLAUDE_CONFIG_DIR": str(self.home("claude-code")),
            "CODEX_HOME": str(self.home("codex")),
            "OPENCODE_CONFIG_DIR": str(self.home("opencode")),
            "PI_CODING_AGENT_DIR": str(self.home("pi")),
            "XDG_CONFIG_HOME": str(self.root / "xdg"),
            **extra,
        }

    def run(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.binary.executable), *args],
            cwd=self.project,
            env=self.environment(**extra),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def setup(self, *args: str) -> None:
        options = [f"--client={client}" for client in CLIENTS]
        result = self.run("setup", *options, *args, "--write", "--json")
        assert result.returncode == 0, result.stdout + result.stderr

    def refresh(self, *, code: int = 0, **extra: str) -> Any:
        result = self.run("setup", "--refresh", "--json", **extra)
        assert result.returncode == code, result.stdout + result.stderr
        return json.loads(result.stdout)

    def calls(self, client: str) -> list[list[str]]:
        log = self.home(client) / "commands.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    def json(self, path: Path) -> Any:
        return json.loads(path.read_text())


def machine(binary: Binary) -> Machine:
    commands = binary.directory / "commands"
    commands.mkdir()
    for name, body in (("claude", CLAUDE), ("codex", CODEX)):
        script = commands / name
        script.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
        script.chmod(0o700)
    for home in HOMES.values():
        (binary.directory / home).mkdir(parents=True)
    project = binary.directory / "project"
    project.mkdir()
    return Machine(binary, project)


def outdated_adapter(path: Path) -> None:
    """Replace an installed adapter with an earlier release's, keeping its argv."""
    found = re.search(r"const ARGV = (\[.*?\]);", path.read_text())
    assert found
    path.write_text(f"// an earlier neosian adapter\nconst ARGV = {found[1]};\n")


def outdated_hooks(path: Path, event: str) -> None:
    """An earlier release that did not register one event."""
    document = json.loads(path.read_text())
    del document["hooks"][event]
    path.write_text(json.dumps(document, indent=4))


def by_client(report: Any) -> dict[str, Any]:
    return {client["client"]: client for client in report["clients"]}


def actions(client: Any) -> dict[str, str]:
    return {part["kind"]: part["action"] for part in client["parts"]}


SERVER = "neosian"
FORMER = "neosian-memory"
RECEIPT = "neosian-registrations.json"


def renamed(members: dict[str, Any], old: str, new: str) -> dict[str, Any]:
    """The same members in the same order, one key renamed."""
    return {(new if key == old else key): value for key, value in members.items()}


def claude_json(host: Machine) -> Path:
    return host.home("claude-code") / ".claude.json"


def as_released(host: Machine) -> None:
    """Claude Code's registration as 2.20.0 wrote it: the same entry under
    `neosian-memory`, and no receipt."""
    path = claude_json(host)
    document = json.loads(path.read_text())
    document["mcpServers"] = renamed(document["mcpServers"], SERVER, FORMER)
    path.write_text(json.dumps(document, indent=1))
    (host.home("claude-code") / RECEIPT).unlink(missing_ok=True)


def claude_project(host: Machine, name: str, members: dict[str, Any]) -> Path:
    """A project directory Claude Code's `.claude.json` records."""
    directory = host.root / name
    directory.mkdir(parents=True, exist_ok=True)
    path = claude_json(host)
    document = json.loads(path.read_text())
    key = str(directory.resolve())
    document.setdefault("projects", {}).setdefault(key, {}).update(members)
    path.write_text(json.dumps(document, indent=1))
    return directory


def migrations(host: Machine) -> list[Path]:
    root = host.binary.home / "migrations"
    return sorted(root.iterdir()) if root.exists() else []
