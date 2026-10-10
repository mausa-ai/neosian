"""The differential harness (DESIGN §39.4, ledger #348): one corpus of
cases, two doors, one seeded home. A case is an argv with its stdin; the
replay runs the Python shell and the Rust binary over fresh copies of the
seeded home and compares what each printed and what each left behind, by
parity's unit: JSON parsed-equal, text byte-equal. Two runs never share a
clock, a temp dir or an id generator, so timestamps, generated ids and
the home's own path compare by shape, never by value; every other byte
is exact. The binary answers only the verbs it has: a case whose verb is
not in `BINARY_VERBS` is skipped, and a verb the binary gains without
joining that list fails the ratchet in test_verbs.py."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

import yaml

ROOT: Final = Path(__file__).resolve().parents[2]
BINARY_ENV: Final = "NEOSIAN_BINARY"
# The verbs the binary answers today; R1 to R4 grow it, and the corpus
# cases for a verb start asserting the day it joins.
BINARY_VERBS: Final[frozenset[str]] = frozenset({"version", "docs"})
Door = Literal["python", "rust"]

_TIMESTAMP: Final = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|\+00:00)"
)
_UUID: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
_RPC_TIMEOUT: Final = 30.0


@dataclass(frozen=True)
class Case:
    """One invocation. `argv` follows the executable, with `{home}`,
    `{spool}`, `{project}`, `{out}`, `{user}` and `{proj}` expanded at run
    time; `payload` is the object a hook sends on stdin (its strings
    expanded too), `stdin` plain text, `rpc` a JSON-RPC conversation held
    with the spawned server instead of a one-shot run."""

    name: str
    argv: tuple[str, ...]
    stdin: str | None = None
    payload: Mapping[str, Any] | None = None
    rpc: tuple[Mapping[str, Any], ...] = ()
    writes: bool = False
    # A verb whose stdout is the shell's human banner compares by the one
    # token both print (`version`: what the installer greps, #340).
    token: re.Pattern[str] | None = None

    @property
    def verb(self) -> str:
        return self.argv[0]

    @property
    def json(self) -> bool:
        return "--json" in self.argv


@dataclass(frozen=True)
class Outcome:
    code: int
    stdout: bytes
    stderr: bytes
    rpc: tuple[Any, ...] = ()


@dataclass(frozen=True)
class Workspace:
    """A door's private copy of the seeded home, the project directory the
    hook argv names, and a scratch directory for exports."""

    home: Path
    project: Path
    out: Path
    user: str
    proj: str

    @property
    def spool(self) -> Path:
        return self.home / "spool"

    def expand(self, text: str) -> str:
        return (
            text.replace("{home}", str(self.home))
            .replace("{spool}", str(self.spool))
            .replace("{project}", str(self.project))
            .replace("{out}", str(self.out))
            .replace("{user}", self.user)
            .replace("{proj}", self.proj)
        )

    def environment(self) -> dict[str, str]:
        # The developer's shell minus any store the environment names;
        # HOME under the workspace so no client config is ever touched.
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("NEOSIAN_") or k == BINARY_ENV
        }
        env["HOME"] = str(self.home.parent)
        env["NEOSIAN_HOME"] = str(self.home)
        env["NO_COLOR"] = "1"
        return env


def python_door() -> Path:
    """The console script beside the interpreter, as a shell meets it."""
    name = "neosian.exe" if sys.platform == "win32" else "neosian"
    script = Path(sys.executable).parent / name
    if not script.is_file():
        raise RuntimeError(f"console script missing: {script} (run `uv sync`)")
    return script


def rust_door() -> Path | None:
    """`NEOSIAN_BINARY`, else the release build, else the debug build;
    None when none exists (the tier then self-skips)."""
    named = os.environ.get(BINARY_ENV)
    candidates = (
        [Path(named)]
        if named
        else [
            ROOT / "target" / "release" / "neosian",
            ROOT / "target" / "debug" / "neosian",
        ]
    )
    return next((path for path in candidates if path.is_file()), None)


def workspace(
    seeded: Path, into: Path, *, user: str, proj: str, project_name: str
) -> Workspace:
    """A fresh copy of the seeded home for one door."""
    into.mkdir(parents=True, exist_ok=True)
    home = into / "home"
    shutil.copytree(seeded, home)
    project = into / project_name
    project.mkdir()
    out = into / "out"
    return Workspace(home=home, project=project, out=out, user=user, proj=proj)


def run_case(executable: Path, case: Case, space: Workspace) -> Outcome:
    """One door over one workspace: the one-shot run, or the RPC session."""
    argv = [str(executable), *(space.expand(token) for token in case.argv)]
    env = space.environment()
    if case.rpc:
        return _run_rpc(argv, case.rpc, space, env)
    stdin = case.stdin
    if case.payload is not None:
        stdin = space.expand(json.dumps(case.payload))
    done = subprocess.run(
        argv,
        input=None if stdin is None else stdin.encode("utf-8"),
        capture_output=True,
        env=env,
        cwd=space.project,
        timeout=120,
        check=False,
    )
    return Outcome(done.returncode, done.stdout, done.stderr)


def _run_rpc(
    argv: list[str],
    messages: tuple[Mapping[str, Any], ...],
    space: Workspace,
    env: Mapping[str, str],
) -> Outcome:
    """A JSON-RPC conversation over the server's stdio, newline-delimited:
    each request waits for the response carrying its id; notifications are
    sent and not awaited. The responses come back in request order."""
    server = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=space.project,
    )
    assert server.stdin is not None and server.stdout is not None
    responses: list[Any] = []
    try:
        for message in messages:
            server.stdin.write(
                (space.expand(json.dumps(message)) + "\n").encode("utf-8")
            )
            server.stdin.flush()
            if "id" not in message:
                continue
            while True:
                line = server.stdout.readline()
                if not line:
                    raise RuntimeError(
                        f"the server closed stdout before answering {message['id']}"
                    )
                answer = json.loads(line)
                if answer.get("id") == message["id"]:
                    responses.append(answer)
                    break
        server.stdin.close()
        _, stderr = server.communicate(timeout=_RPC_TIMEOUT)
    finally:
        if server.poll() is None:
            server.kill()
            server.wait()
    return Outcome(server.returncode, b"", stderr, tuple(responses))


def normalise(text: str, space: Workspace, *, keep: frozenset[str]) -> str:
    """The shape of a text: the workspace's paths, timestamps and generated
    ids replaced by tokens, the ids the corpus itself names kept."""
    for path, token in (
        (space.home, "<HOME>"),
        (space.project, "<PROJECT>"),
        (space.out, "<OUT>"),
        (space.home.parent, "<SPACE>"),
    ):
        for spelling in {str(path), str(path.resolve())}:
            text = text.replace(spelling, token)
    text = _TIMESTAMP.sub("<TS>", text)
    return _UUID.sub(lambda m: m.group(0) if m.group(0) in keep else "<ID>", text)


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="surrogateescape")


def shape_of_output(
    case: Case, outcome: Outcome, space: Workspace, *, keep: frozenset[str]
) -> Any:
    """What a door printed, by parity's unit: parsed JSON for `--json` and
    RPC answers, the normalised bytes for everything else."""
    stderr = normalise(_decode(outcome.stderr), space, keep=keep)
    if case.rpc:
        answers = [
            json.loads(normalise(json.dumps(a), space, keep=keep)) for a in outcome.rpc
        ]
        return (outcome.code, answers, stderr)
    stdout = normalise(_decode(outcome.stdout), space, keep=keep)
    if case.token is not None:
        found = case.token.search(stdout)
        return (outcome.code, None if found is None else found.group(0), stderr)
    if case.json:
        parsed = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        return (outcome.code, parsed, stderr)
    return (outcome.code, stdout, stderr)


def snapshot(space: Workspace, *, keep: frozenset[str]) -> dict[str, Any]:
    """What a door left in the home: JSONL rows parsed, a document's
    frontmatter parsed and its body exact, locks by presence, the rest
    exact; paths normalised like contents."""
    files: dict[str, Any] = {}
    for path in sorted(p for p in space.home.rglob("*") if p.is_file()):
        rel = normalise(path.relative_to(space.home).as_posix(), space, keep=keep)
        if path.name.endswith(".lock"):
            files[rel] = "<lock>"
            continue
        text = normalise(_decode(path.read_bytes()), space, keep=keep)
        if path.suffix == ".jsonl":
            files[rel] = [
                json.loads(line) for line in text.splitlines() if line.strip()
            ]
        elif path.suffix == ".md":
            files[rel] = _document(text)
        else:
            files[rel] = text
    return files


def _document(text: str) -> tuple[Any, str]:
    """The Python reader's split: whole-line fences, the frontmatter as
    YAML, the body stripped."""
    lines = text.strip().split("\n")
    if lines[0].rstrip("\r") != "---":
        return (None, text)
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r") == "---":
            return (
                yaml.safe_load("\n".join(lines[1:index])),
                "\n".join(lines[index + 1 :]).strip(),
            )
    return (None, text)


def binary_verbs(executable: Path) -> frozenset[str]:
    """The subcommands the binary's `--help` lists."""
    help_text = subprocess.run(
        [str(executable), "--help"], capture_output=True, text=True, check=True
    ).stdout
    verbs: set[str] = set()
    listing = False
    for line in help_text.splitlines():
        if line.startswith("Commands:"):
            listing = True
            continue
        if listing:
            if not line.strip():
                break
            verbs.add(line.split()[0])
    return frozenset(verbs)
