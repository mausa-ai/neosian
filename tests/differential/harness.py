"""The differential harness (DESIGN §39.4, ledger #348): one corpus of
cases, two doors, one seeded home. A case is an argv with its stdin; the
replay runs the Python shell and the Rust binary over fresh copies of the
seeded home and compares what each printed and what each left behind, by
parity's unit: JSON parsed-equal, text byte-equal. Two runs never share a
clock, a temp dir or an id generator, so timestamps, generated ids and
the home's own path compare by shape, never by value; every other byte
is exact. The binary answers only the verbs it has: a case whose verb is
not in `BINARY_VERBS` is skipped, and a verb the binary gains without
joining that list fails the ratchet in test_verbs.py. One text is each
grammar's own: at exit 2 argparse prints a usage block before the
refusal's sentence, so the block is dropped and the sentence compared.

A `serve` case (R2, #368) spawns each door's state process over its copy
of the home on a free port, replays an exchange list against it and
compares the answers (status, parsed body, whether a session id rode the
answer) and the home after SIGTERM; the server's exit status and its log
are each door's own and stay out of the comparison. A `next_cursor` is
compared decoded, since the history's keyset carries a timestamp. An
`/mcp` answer (slice B, #371) is its SSE frames parsed, and `{session:N}`
in a header is the session id the N-th answer minted, compared by its
presence alone."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

import httpx
import yaml

ROOT: Final = Path(__file__).resolve().parents[2]
BINARY_ENV: Final = "NEOSIAN_BINARY"
# The bearer the corpus's state processes take and its clients present.
TOKEN: Final = "differential-token"
# A verb with subcommands is told apart by each of them (`memory view`),
# so a store's reads can assert before its writes exist.
SUBCOMMANDS: Final[frozenset[str]] = frozenset({"memory"})
# The verbs the binary answers today; R1 to R4 grow it, and the corpus
# cases for a verb start asserting the day it joins.
BINARY_VERBS: Final[frozenset[str]] = frozenset(
    {
        "version",
        "docs",
        "search",
        "continue",
        "record",
        "mcp",
        "serve",
        "setup",
        "status",
        "configure",
        *(
            f"memory {command}"
            for command in (
                "view",
                "create",
                "str_replace",
                "insert",
                "delete",
                "rename",
                "versions",
                "redact",
                "revert",
            )
        ),
    }
)
Door = Literal["python", "rust"]

_TIMESTAMP: Final = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|\+00:00)"
)
_UUID: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
# argparse refuses with a usage block, then `<prog>: error: <sentence>`.
# The block is the Python grammar's own; the sentence is the doors' to share.
_ARGPARSE: Final = re.compile(
    r"\Ausage: .*?^[^\n]*?: error: ", re.DOTALL | re.MULTILINE
)
_CURSOR_OF: Final = re.compile(r"\A\{cursor:(\d+)\}\Z")
_SESSION_OF: Final = re.compile(r"\A\{session:(\d+)\}\Z")
SESSION_HEADER: Final = "mcp-session-id"
_RPC_TIMEOUT: Final = 30.0
_BOOT_TIMEOUT: Final = 30.0
_HTTP_TIMEOUT: Final = 30.0


@dataclass(frozen=True)
class Exchange:
    """One request of a `serve` case: the method, the path, the body (a
    JSON object with its strings expanded, `{cursor:N}` the `next_cursor`
    the N-th answer carried; or raw bytes; or none), the bearer presented
    (None: no header) and the headers, `{session:N}` the session id the
    N-th answer minted."""

    method: str
    path: str
    body: Mapping[str, Any] | bytes | None = None
    token: str | None = TOKEN
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Case:
    """One invocation. `argv` follows the executable, with `{home}`,
    `{spool}`, `{project}`, `{out}`, `{user}`, `{proj}`, `{session}` (the
    seed's newest conversation) and `{port}` expanded at run time;
    `payload` is the object a hook sends on stdin (its strings expanded
    too), `stdin` plain text, `rpc` a JSON-RPC conversation held with the
    spawned server, `http` an exchange list held with the spawned state
    process; `env` is laid over the workspace's environment, expanded."""

    name: str
    argv: tuple[str, ...]
    stdin: str | None = None
    payload: Mapping[str, Any] | None = None
    rpc: tuple[Mapping[str, Any], ...] = ()
    http: tuple[Exchange, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    writes: bool = False
    # A verb whose stdout is the shell's human banner compares by the one
    # token both print (`version`: what the installer greps, #340).
    token: re.Pattern[str] | None = None

    @property
    def verb(self) -> str:
        head = self.argv[0]
        return f"{head} {self.argv[1]}" if head in SUBCOMMANDS else head

    @property
    def json(self) -> bool:
        return "--json" in self.argv


@dataclass(frozen=True)
class Outcome:
    code: int
    stdout: bytes
    stderr: bytes
    rpc: tuple[Any, ...] = ()
    http: tuple[Any, ...] = ()


@dataclass(frozen=True)
class Workspace:
    """A door's private copy of the seeded home, the project directory the
    hook argv names, a scratch directory for exports, and the seed's
    scopes and newest session."""

    home: Path
    project: Path
    out: Path
    user: str
    proj: str
    session: str

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
            .replace("{session}", self.session)
        )

    def environment(self) -> dict[str, str]:
        # The developer's shell minus any store the environment names;
        # HOME under the workspace so no client config is ever touched.
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("NEOSIAN_") or k == BINARY_ENV
        }
        # Client overrides and credentials must never escape the fake HOME.
        for name in tuple(env):
            if name.endswith("_API_KEY") or name in {
                "CLAUDE_CONFIG_DIR",
                "CODEX_HOME",
                "OPENCODE_CONFIG_DIR",
                "PI_CODING_AGENT_DIR",
                "XDG_CONFIG_HOME",
                "APPDATA",
                "LOCALAPPDATA",
            }:
                del env[name]
        env["HOME"] = str(self.home.parent)
        env["USERPROFILE"] = str(self.home.parent)
        env["NEOSIAN_HOME"] = str(self.home)
        # The login the seed's scopes were derived from, whatever the
        # runner's own environment names (the layout reads it first).
        env["LOGNAME"] = self.user.partition(":")[2]
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
    seeded: Path,
    into: Path,
    *,
    user: str,
    proj: str,
    session: str,
    project_name: str,
) -> Workspace:
    """A fresh copy of the seeded home for one door."""
    into.mkdir(parents=True, exist_ok=True)
    home = into / "home"
    shutil.copytree(seeded, home)
    project = into / project_name
    project.mkdir()
    out = into / "out"
    return Workspace(
        home=home, project=project, out=out, user=user, proj=proj, session=session
    )


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


def wait_healthy(server: subprocess.Popen[bytes], url: str) -> None:
    """Until `/health` answers, or the process gives up first."""
    deadline = time.monotonic() + _BOOT_TIMEOUT
    while True:
        if server.poll() is not None:
            _, err = server.communicate()
            raise RuntimeError(f"serve exited early: {_decode(err)}")
        try:
            if httpx.get(f"{url}/health", timeout=1.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            raise RuntimeError("serve never became healthy")
        time.sleep(0.05)


def run_case(executable: Path, case: Case, space: Workspace) -> Outcome:
    """One door over one workspace: the one-shot run, the RPC session, or
    the state process and its exchanges."""
    argv = [str(executable), *(space.expand(token) for token in case.argv)]
    env = space.environment()
    env.update({name: space.expand(value) for name, value in case.env.items()})
    if case.rpc:
        return _run_rpc(argv, case.rpc, space, env)
    if case.http:
        return _run_http(argv, case.http, space, env)
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
        # communicate() closes stdin itself (EOF ends the server); closing
        # it here first made the flush inside communicate() raise.
        _, stderr = server.communicate(timeout=_RPC_TIMEOUT)
    finally:
        if server.poll() is None:
            server.kill()
            server.wait()
    return Outcome(server.returncode, b"", stderr, tuple(responses))


def _with_cursors(body: Mapping[str, Any], answers: list[tuple[int, Any, bool]]) -> Any:
    """`{cursor:N}` in a body: the `next_cursor` the N-th answer carried."""
    resolved = dict(body)
    for name, value in body.items():
        found = _CURSOR_OF.match(value) if isinstance(value, str) else None
        if found is not None:
            resolved[name] = answers[int(found.group(1))][1]["next_cursor"]
    return resolved


def _with_sessions(value: str, sessions: list[str | None]) -> str:
    """`{session:N}` in a header: the session id the N-th answer minted."""
    found = _SESSION_OF.match(value)
    if found is None:
        return value
    minted = sessions[int(found.group(1))]
    if minted is None:
        raise RuntimeError(f"answer {found.group(1)} minted no session")
    return minted


def _parsed(response: httpx.Response) -> Any:
    """A body by its kind: an SSE answer as its frames' JSON, a JSON body
    parsed, anything else as text."""
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        return [
            json.loads(line[len("data: ") :])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
    try:
        return response.json()
    except ValueError:
        return response.text


def _run_http(
    argv: list[str],
    exchanges: tuple[Exchange, ...],
    space: Workspace,
    env: Mapping[str, str],
) -> Outcome:
    """The state process on a free port, the exchanges in order, SIGTERM."""
    port = free_port()
    argv = [token.replace("{port}", str(port)) for token in argv]
    url = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=space.project,
    )
    answers: list[tuple[int, Any, bool]] = []
    sessions: list[str | None] = []
    try:
        wait_healthy(server, url)
        with httpx.Client(base_url=url, timeout=_HTTP_TIMEOUT) as http:
            for exchange in exchanges:
                headers = {
                    name: _with_sessions(value, sessions)
                    for name, value in exchange.headers.items()
                }
                if exchange.token is not None:
                    headers["Authorization"] = f"Bearer {exchange.token}"
                content: bytes | None = None
                if isinstance(exchange.body, Mapping):
                    body = _with_cursors(exchange.body, answers)
                    content = space.expand(json.dumps(body)).encode("utf-8")
                elif exchange.body is not None:
                    content = exchange.body
                response = http.request(
                    exchange.method, exchange.path, content=content, headers=headers
                )
                session = response.headers.get(SESSION_HEADER)
                sessions.append(session)
                answers.append(
                    (response.status_code, _parsed(response), session is not None)
                )
    finally:
        server.terminate()
        try:
            _, stderr = server.communicate(timeout=_RPC_TIMEOUT)
        except subprocess.TimeoutExpired:
            server.kill()
            _, stderr = server.communicate()
    return Outcome(server.returncode, b"", stderr, http=tuple(answers))


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


def _cursors_decoded(value: Any) -> Any:
    """A `next_cursor` as the keyset it encodes, its timestamp by shape;
    every other value as it is."""
    if isinstance(value, list):
        return [_cursors_decoded(item) for item in value]
    if not isinstance(value, dict):
        return value
    shaped: dict[str, Any] = {}
    for name, item in value.items():
        if name == "next_cursor" and isinstance(item, str):
            parts = json.loads(base64.urlsafe_b64decode(item.encode("ascii")))
            shaped[name] = [
                _TIMESTAMP.sub("<TS>", part) if isinstance(part, str) else part
                for part in parts
            ]
        else:
            shaped[name] = _cursors_decoded(item)
    return shaped


def shape_of_output(
    case: Case, outcome: Outcome, space: Workspace, *, keep: frozenset[str]
) -> Any:
    """What a door printed, by parity's unit: parsed JSON for `--json`, RPC
    and HTTP answers, the normalised bytes for everything else."""
    if case.http:
        answers = json.loads(normalise(json.dumps(outcome.http), space, keep=keep))
        return _cursors_decoded(answers)
    stderr = normalise(_decode(outcome.stderr), space, keep=keep)
    stderr = _ARGPARSE.sub("error: ", stderr)
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
        if case.verb == "status" and outcome.code == 0:
            # A real distinction between the doors, like version's banner:
            # validate each installation's command before comparing findings.
            # test_configuration also pins which shape each door reports.
            upgrades = {
                "binary": "neosian-update",
                "project": "uv add 'neosian==<version>'",
            }
            for report in parsed:
                assert report["upgrade"] == upgrades[report["shape"]]
                report["shape"] = "<INSTALLATION>"
                report["upgrade"] = "<UPGRADE>"
        return (outcome.code, parsed, stderr)
    return (outcome.code, stdout, stderr)


def snapshot(space: Workspace, *, keep: frozenset[str]) -> dict[str, Any]:
    """What a door left in the home: JSONL rows parsed, a document's
    frontmatter parsed and its body exact, locks by presence, the rest
    exact; paths normalised like contents. Files whose names are generated
    ids share one normalised path (two messages in a scope), so each path
    holds its files as a group ordered by content, never by the ids."""
    files: dict[str, list[Any]] = {}
    for path in sorted(p for p in space.home.rglob("*") if p.is_file()):
        rel = normalise(path.relative_to(space.home).as_posix(), space, keep=keep)
        if path.name.endswith(".lock"):
            files.setdefault(rel, []).append("<lock>")
            continue
        text = normalise(_decode(path.read_bytes()), space, keep=keep)
        if path.suffix == ".jsonl":
            entry: Any = [
                json.loads(line) for line in text.splitlines() if line.strip()
            ]
        elif path.suffix == ".md":
            entry = _document(text)
        elif path.suffix == ".toml":
            try:
                raw = tomllib.loads(path.read_text())
                entry = json.loads(
                    normalise(json.dumps(raw, default=str), space, keep=keep)
                )
            except tomllib.TOMLDecodeError:
                entry = text
        else:
            entry = text
        files.setdefault(rel, []).append(entry)
    return {
        rel: sorted(group, key=lambda entry: json.dumps(entry, sort_keys=True))
        for rel, group in files.items()
    }


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


def _listed(executable: Path, *verb: str) -> list[str]:
    """The subcommands a `--help` lists under `Commands:`."""
    help_text = subprocess.run(
        [str(executable), *verb, "--help"], capture_output=True, text=True, check=True
    ).stdout
    listed: list[str] = []
    listing = False
    for line in help_text.splitlines():
        if line.startswith("Commands:"):
            listing = True
            continue
        if listing:
            if not line.strip():
                break
            listed.append(line.split()[0])
    return listed


def binary_verbs(executable: Path) -> frozenset[str]:
    """The verbs the binary's `--help` lists, a verb with subcommands by
    each of them."""
    verbs: set[str] = set()
    for verb in _listed(executable):
        if verb in SUBCOMMANDS:
            verbs.update(f"{verb} {sub}" for sub in _listed(executable, verb))
        else:
            verbs.add(verb)
    return frozenset(verbs)
