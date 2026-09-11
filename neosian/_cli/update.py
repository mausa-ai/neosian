"""`neosian update` and the update knob (DESIGN §30.3, ledger #214).

One config knob, `[update] mode = off | notify | auto`, default `off`.
The check reads PyPI's simple index (the release axis, never
neosian.com) and runs only on the human door — bare `neosian`, `chat`,
`status`, `playground`, `configure`, on a terminal, never under `--json`
— never on the agent verbs: `record` runs on every hook event and `mcp`
is spawned by clients, often without network. Throttled to once per
24 h by a stamp under the home, a 2 s timeout, silent when offline.
`notify` prints one stderr line naming the release and the exact
command; `auto` applies, fenced three ways — the uv tool shape only,
within the installed major, never from a stable to a pre-release — runs
the one command it would have printed, then re-executes itself so
nothing runs on mixed files. The installer pins `neosian==<release>`,
so the printed command is `uv tool install "neosian==<latest>"`.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, TextIO

import httpx

from neosian import __version__
from neosian._cli.config import get_section, set_value
from neosian._cli.shape import UV_TOOL, Shape, detect_shape
from neosian._foundation.memory.home import home
from neosian._foundation.memory.settings import StreamParser
from neosian._foundation.shared.fileio import atomic_write, private_mkdir

MODES: Final = ("off", "notify", "auto")
DEFAULT_MODE: Final = "off"
HUMAN_VERBS: Final = frozenset({"chat", "status", "playground", "configure"})
INDEX_URL: Final = "https://pypi.org/simple/neosian/"
_ACCEPT: Final = "application/vnd.pypi.simple.v1+json"
TIMEOUT_SECONDS: Final = 2.0
STAMP_NAME: Final = "update-check"
CHECK_INTERVAL: Final = timedelta(hours=24)
_VERSION: Final = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:rc(\d+))?$")
_FILE: Final = re.compile(r"^neosian-([0-9][^-]*?)(?:-py3-none-any\.whl|\.tar\.gz)$")
_DESCRIPTION: Final = "Check PyPI for a newer neosian; print the command, or apply it."
_EPILOG: Final = (
    "The knob: [update] mode = off | notify | auto in <home>/config.toml (set "
    "it with --mode). The check runs on the human verbs only, once per 24 h; "
    "auto applies only a uv tool install, within the installed major, never a "
    "pre-release over a stable."
)


@dataclass(frozen=True, slots=True, order=True)
class Version:
    """`X.Y.Z[rcN]` — the release axis; a final sorts above its rcs."""

    major: int
    minor: int
    patch: int
    final: bool
    rc: int

    @classmethod
    def parse(cls, text: str) -> Version | None:
        match = _VERSION.match(text)
        if match is None:
            return None
        major, minor, patch, rc = match.groups()
        return cls(int(major), int(minor), int(patch), rc is None, int(rc or 0))

    @property
    def prerelease(self) -> bool:
        return not self.final

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        return base if self.final else f"{base}rc{self.rc}"


def latest_release(
    transport: httpx.BaseTransport | None = None, *, timeout: float = TIMEOUT_SECONDS
) -> Version | None:
    """The newest release on the index, yanked files skipped; None when
    the index is unreachable or unreadable — silence, never a traceback."""
    try:
        with httpx.Client(transport=transport, timeout=timeout) as client:
            response = client.get(INDEX_URL, headers={"Accept": _ACCEPT})
            response.raise_for_status()
            files: Any = response.json()["files"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None
    versions: list[Version] = []
    for entry in files if isinstance(files, list) else []:
        if not isinstance(entry, dict) or entry.get("yanked"):
            continue
        match = _FILE.match(str(entry.get("filename", "")))
        version = Version.parse(match.group(1)) if match else None
        if version is not None:
            versions.append(version)
    return max(versions) if versions else None


@dataclass(frozen=True, slots=True)
class Stamp:
    checked_at: datetime
    latest: str


def read_stamp(root: Path) -> Stamp | None:
    path = root / STAMP_NAME
    try:
        checked, latest = path.read_text(encoding="utf-8").split()
        return Stamp(datetime.fromisoformat(checked), latest)
    except (OSError, ValueError):
        return None


def write_stamp(root: Path, latest: Version, *, now: datetime) -> None:
    private_mkdir(root)
    atomic_write(root / STAMP_NAME, f"{now.isoformat()} {latest}\n")


@dataclass(frozen=True, slots=True)
class Decision:
    """What the check concluded: `current`, `notify`, `apply`, `refuse`
    or `unreachable`, with the one line to print."""

    action: str
    line: str
    latest: str | None = None


def plan(
    current: str, latest: Version | None, shape: Shape, *, applying: bool
) -> Decision:
    """The release axis against this install, and the three fences when
    applying: the uv tool shape only, within the major, never a
    pre-release over a stable."""
    if latest is None:
        return Decision("unreachable", "PyPI is unreachable; nothing checked")
    installed = Version.parse(current)
    if installed is not None and latest <= installed:
        return Decision("current", f"neosian {current} is current", str(latest))
    command = shape.upgrade_line(str(latest))
    notice = f"neosian {latest} is available (you have {current}): {command}"
    if not applying:
        return Decision("notify", notice, str(latest))
    if shape.kind != UV_TOOL:
        why = f"this install is a {shape.kind}, not a uv tool"
    elif installed is not None and latest.major != installed.major:
        why = f"{latest} is a new major"
    elif latest.prerelease and (installed is None or installed.final):
        why = f"{latest} is a pre-release and {current} is not"
    else:
        return Decision("apply", command, str(latest))
    return Decision(
        "refuse", f"not applied ({why}); run it yourself: {command}", str(latest)
    )


def _apply(command: str) -> int:
    """Run the one command, then re-execute this very invocation on the
    new files (the stamp keeps the re-run from checking again)."""
    code = subprocess.run(shlex.split(command), check=False).returncode
    if code != 0:
        return code
    os.execv(sys.argv[0], sys.argv)
    raise AssertionError  # pragma: no cover - execv does not return


Apply = Callable[[str], int]


def current_mode() -> str:
    mode = get_section("update").get("mode", DEFAULT_MODE)
    return str(mode) if mode in MODES else DEFAULT_MODE


def human_door(
    subcommand: str | None, *, on_terminal: bool, argv: Sequence[str]
) -> bool:
    """Where the check may run: bare `neosian` or a human verb, on a
    terminal, not under `--json` — never an agent verb."""
    if not on_terminal or "--json" in argv:
        return False
    return subcommand is None or subcommand in HUMAN_VERBS


def check_on_the_human_door(
    env: Mapping[str, str],
    *,
    err: TextIO,
    transport: httpx.BaseTransport | None = None,
    now: datetime | None = None,
    prefix: Path | None = None,
    apply: Apply = _apply,
) -> Decision | None:
    """The knob's act at the human door: nothing under `off`; one line
    under `notify`; under `auto` the fenced apply, then the re-exec."""
    mode = current_mode()
    if mode == DEFAULT_MODE:
        return None
    now = datetime.now(UTC) if now is None else now
    root = home(env)
    stamp = read_stamp(root)
    if stamp is not None and now - stamp.checked_at < CHECK_INTERVAL:
        latest = Version.parse(stamp.latest)
    else:
        latest = latest_release(transport)
        if latest is None:
            return None  # offline: silent
        write_stamp(root, latest, now=now)
    shape = detect_shape(Path(sys.prefix) if prefix is None else prefix, env)
    decision = plan(__version__, latest, shape, applying=mode == "auto")
    if decision.action in ("notify", "refuse"):
        err.write(f"{decision.line}\n")
    elif decision.action == "apply":
        err.write(f"neosian {decision.latest}: {decision.line}\n")
        apply(decision.line)
    return decision


def run_update(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    out: TextIO,
    err: TextIO,
    transport: httpx.BaseTransport | None = None,
    prefix: Path | None = None,
    apply: Apply = _apply,
    prog: str = "neosian update",
) -> int:
    """`update [--check] [--write] [--json] [--mode M]`: exit 0 printed or
    applied, 1 refused or unreachable, 2 grammar."""
    parser = StreamParser(prog=prog, description=_DESCRIPTION, epilog=_EPILOG)
    parser.bind(out, err)
    parser.add_argument(
        "--check", action="store_true", help="check and print (the default)"
    )
    parser.add_argument("--write", action="store_true", help="apply, fenced")
    parser.add_argument(
        "--json", action="store_true", dest="json_output", help="one JSON object"
    )
    parser.add_argument("--mode", choices=MODES, help="set the knob and stop")
    try:
        args = parser.parse_args(list(argv))
    except SystemExit as exc:  # argparse: usage already on the streams
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 2
    if args.mode is not None:
        set_value("update", "mode", args.mode)
        payload: dict[str, Any] = {"mode": args.mode}
        line = f"update mode {args.mode}"
        out.write(json.dumps(payload) + "\n" if args.json_output else line + "\n")
        return 0
    mode = current_mode()
    shape = detect_shape(Path(sys.prefix) if prefix is None else prefix, env)
    latest = latest_release(transport)
    if latest is not None:
        write_stamp(home(env), latest, now=datetime.now(UTC))
    applying = args.write or mode == "auto"
    decision = plan(__version__, latest, shape, applying=applying)
    if args.json_output:
        payload = {
            "current": __version__,
            "latest": decision.latest,
            "shape": shape.kind,
            "mode": mode,
            "action": decision.action,
            "line": decision.line,
        }
        out.write(json.dumps(payload) + "\n")
    else:
        out.write(decision.line + "\n")
    if decision.action == "apply":
        return apply(decision.line)
    return 1 if decision.action in ("refuse", "unreachable") else 0
