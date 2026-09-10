"""`neosian status` — is this machine set up? (DESIGN §30)

The home and whether it exists; the config and which providers have a key
(names and sources, never values); this directory's two scopes; per
client, whether it is installed, registered for MCP, carrying the hooks,
and whether the interpreter those files name still resolves (the
moved-venv failure, silent until now); the last recorded session; the
one-writer note; the installation shape with its upgrade line; the
update knob. Exit 0 whenever it ran — findings are data. Pure over an
injected `Environment`; the only store access is one read of the home,
and only when the home exists.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, TextIO

from neosian import __version__
from neosian._cli.config import get_config_path, get_section
from neosian._cli.providers import key_source, provider_keys
from neosian._cli.shape import Shape, detect_shape
from neosian._foundation.mcp.install import SERVER_NAME, resolve_target as mcp_target
from neosian._foundation.memory.home import home, project_mounts
from neosian._foundation.memory.settings import StreamParser
from neosian._foundation.record.install import resolve_target as hook_target
from neosian._foundation.record.span import SESSIONS_DIR
from neosian._foundation.shared.client_config import Environment
from neosian._foundation.shared.exceptions import ConfigurationError, NeosianError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_DESCRIPTION: Final = "Is this machine set up? The home, the keys, the clients."
CLIENTS: Final = ("claude-code", "codex", "opencode")
_RECORD_MARKER: Final = "-m neosian.record"
_PLUGIN_ARGV: Final = re.compile(r'\[[^\[\]]*"-m", "neosian\.record"[^\[\]]*\]')
_DEFAULT_MODE: Final = "off"
_ONE_WRITER: Final = (
    "{client}: the hooks and the MCP server both write {root} directly — one "
    "writer per root (DESIGN §8): run `neosian serve` and install both with --url"
)


@dataclass(frozen=True, slots=True)
class ClientStatus:
    client: str
    label: str
    installed: bool
    mcp_registered: bool
    hooks_present: bool
    interpreter: str | None  # the command the files name, when they name one
    interpreter_resolves: bool | None
    root: str | None  # the --root both entries name, for the one-writer note


@dataclass(frozen=True, slots=True)
class Status:
    version: str
    home: str
    home_exists: bool
    config_path: str
    config_exists: bool
    providers: tuple[dict[str, str | None], ...]
    scopes: dict[str, str] | None
    scopes_error: str | None
    clients: tuple[ClientStatus, ...]
    last_session: dict[str, str] | None
    one_writer: tuple[str, ...]
    shape: str
    upgrade: str
    update_mode: str


def _load(path: Path) -> dict[str, Any] | None:
    """A client's config as a dict — JSON or TOML by suffix; None when it
    is missing or not a document (status reports, never repairs)."""
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        raw: Any = tomllib.loads(text) if path.suffix == ".toml" else json.loads(text)
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _argv_of(entry: Any) -> list[str] | None:
    """The registered server's argv: `{command, args}` or OpenCode's
    `{command: [...]}`."""
    if not isinstance(entry, dict):
        return None
    command = entry.get("command")
    if isinstance(command, list):
        return [str(part) for part in command]
    if isinstance(command, str):
        args = entry.get("args", [])
        return [command, *(str(a) for a in args)] if isinstance(args, list) else None
    return None


def _hook_argv(document: dict[str, Any]) -> list[str] | None:
    hooks = document.get("hooks")
    if not isinstance(hooks, dict):
        return None
    for groups in hooks.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            for hook in group.get("hooks", []) if isinstance(group, dict) else []:
                command = hook.get("command", "") if isinstance(hook, dict) else ""
                if _RECORD_MARKER in str(command):
                    return shlex.split(str(command))
    return None


def _plugin_argv(path: Path) -> list[str] | None:
    if not path.is_file():
        return None
    match = _PLUGIN_ARGV.search(path.read_text(encoding="utf-8"))
    if match is None:
        return None
    try:
        parsed: Any = json.loads(match.group(0))
    except ValueError:
        return None
    return [str(p) for p in parsed] if isinstance(parsed, list) else None


def _root_of(argv: Sequence[str] | None) -> str | None:
    if argv is None or "--root" not in argv:
        return None
    index = list(argv).index("--root")
    return argv[index + 1] if index + 1 < len(argv) else None


def _resolves(command: str) -> bool:
    path = Path(command)
    return path.is_file() if path.is_absolute() else shutil.which(command) is not None


def client_status(client: str, context: Environment) -> ClientStatus:
    mcp = mcp_target(client, context)
    hooks = hook_target(client, context)
    installed = mcp.evidence_dir.is_dir()
    document = _load(mcp.config_path)
    servers = document.get(mcp.servers_key) if document is not None else None
    server_argv = (
        _argv_of(servers.get(SERVER_NAME)) if isinstance(servers, dict) else None
    )
    if hooks.plugin:
        hook_argv = _plugin_argv(hooks.config_path)
    else:
        hooks_document = _load(hooks.config_path)
        hook_argv = _hook_argv(hooks_document) if hooks_document is not None else None
    interpreter = next((a[0] for a in (server_argv, hook_argv) if a), None)
    roots = {_root_of(server_argv), _root_of(hook_argv)}
    root = roots.pop() if len(roots) == 1 and None not in roots else None
    return ClientStatus(
        client=client,
        label=mcp.label,
        installed=installed,
        mcp_registered=server_argv is not None,
        hooks_present=hook_argv is not None,
        interpreter=interpreter,
        interpreter_resolves=None if interpreter is None else _resolves(interpreter),
        root=root,
    )


async def last_session(root: Path, scope: str) -> dict[str, str] | None:
    """The newest sessions document of `scope` on the home — read only
    when the home exists (opening a FileStore creates its root)."""
    if not root.is_dir():
        return None
    from neosian._foundation.memory.file import FileStore

    store = FileStore(root)
    try:
        entries = await store.list_documents(scope, prefix=f"{SESSIONS_DIR}/")
        live = [e for e in entries if not e.redacted]
        if not live:
            return None
        newest = max(live, key=lambda e: e.updated_at)
        document = await store.read(scope, newest.path)
    except NeosianError:
        return None
    agent = "-"
    for line in (document.content if document else "").splitlines():
        if line.startswith("- agent: "):
            agent = line.removeprefix("- agent: ").strip()
    return {
        "conversation": newest.path.removeprefix(f"{SESSIONS_DIR}/"),
        "agent": agent,
        "updated_at": newest.updated_at.isoformat().replace("+00:00", "Z"),
        "path": newest.path,
    }


async def collect(context: Environment, env: Mapping[str, str]) -> Status:
    root = home(env)
    scopes: dict[str, str] | None = None
    scopes_error: str | None = None
    session: dict[str, str] | None = None
    try:
        mounts = project_mounts(context.cwd)
        scopes = {f"/{m.mount_path}": m.scope for m in mounts}
        session = await last_session(root, mounts[1].scope)
    except ConfigurationError as exc:
        scopes_error = exc.message
    clients = tuple(client_status(client, context) for client in CLIENTS)
    shape = detect_shape(Path(sys.prefix), env)
    config_path = get_config_path()
    mode = get_section("update").get("mode", _DEFAULT_MODE)
    return Status(
        version=__version__,
        home=str(root),
        home_exists=root.is_dir(),
        config_path=str(config_path),
        config_exists=config_path.is_file(),
        providers=tuple(
            {"name": row.name, "env": row.env, "source": key_source(row, env)}
            for row in provider_keys()
        ),
        scopes=scopes,
        scopes_error=scopes_error,
        clients=clients,
        last_session=session,
        one_writer=tuple(
            _ONE_WRITER.format(client=c.client, root=c.root)
            for c in clients
            if c.mcp_registered and c.hooks_present and c.root is not None
        ),
        shape=shape.kind,
        upgrade=Shape(shape.kind).upgrade_line("<version>"),
        update_mode=str(mode),
    )


def _client_line(client: ClientStatus) -> str:
    if not client.installed:
        return f"  {client.client:<12}not installed"
    cells = [
        "mcp registered" if client.mcp_registered else "mcp -",
        "hooks present" if client.hooks_present else "hooks -",
    ]
    if client.interpreter_resolves is False:
        cells.append(f"interpreter missing: {client.interpreter}")
    elif client.interpreter_resolves:
        cells.append("interpreter ok")
    return f"  {client.client:<12}installed  " + "  ".join(cells)


def render_text(status: Status) -> str:
    keyed = [f"{p['name']} ({p['source']})" for p in status.providers if p["source"]]
    lines = [
        f"neosian {status.version}  {status.shape}  upgrade: {status.upgrade}",
        f"home      {status.home}  ({'exists' if status.home_exists else 'missing'})",
        f"config    {status.config_path}  "
        f"({'exists' if status.config_exists else 'missing'})",
        f"keys      {', '.join(keyed) if keyed else 'none — neosian configure'}",
    ]
    if status.scopes is not None:
        pairs = "  ".join(f"{k} = {v}" for k, v in status.scopes.items())
        lines.append(f"scopes    {pairs}")
    else:
        lines.append(f"scopes    {status.scopes_error}")
    lines.append("clients")
    lines += [_client_line(c) for c in status.clients]
    if status.last_session is not None:
        s = status.last_session
        lines.append(
            f"session   {s['agent']} {s['conversation']}  {s['updated_at']}  "
            f"(/project/{s['path']})"
        )
    else:
        lines.append("session   none recorded")
    for note in status.one_writer:
        lines.append(f"note      {note}")
    lines.append(f"update    mode {status.update_mode}")
    return "\n".join(lines) + "\n"


def render_json(status: Status) -> str:
    return json.dumps(asdict(status), ensure_ascii=False) + "\n"


async def run(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    context: Environment,
    out: TextIO,
    err: TextIO,
    prog: str = "neosian status",
) -> int:
    parser = StreamParser(prog=prog, description=_DESCRIPTION)
    parser.bind(out, err)
    parser.add_argument(
        "--json", action="store_true", dest="json_output", help="one JSON object"
    )
    try:
        args = parser.parse_args(list(argv))
    except SystemExit as exc:  # argparse: usage already on the streams
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 2
    status = await collect(context, env)
    out.write(render_json(status) if args.json_output else render_text(status))
    return 0
