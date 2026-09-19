"""`neosian setup` — wire every installed client to this store (DESIGN
§30.3, §22.6).

Detects the clients present on this machine (each installer target's
evidence directory) and runs both installers for each: the MCP
registration and the record hooks, once per machine by default
(`--level project` for this directory's files). It prints what would land
and applies it with `--write`. A file the client's own CLI writes (Claude
Code's user scope, Codex's TOML) is applied by running that CLI when it is
on PATH, and reported as the line to run when it is not. `--root` and
`--url` reach both installers, so moving every client on the machine to
the state process is one run. `--client` narrows; `--json` one object with
both envelopes per client. Exit 0, or 1 when anything was refused or is
left for the user (every client is still reported).
"""

from __future__ import annotations

import io
import json
import shlex
import shutil
import subprocess
from typing import TYPE_CHECKING, Any, Final, TextIO

from neosian._foundation.mcp.install import (
    displace,
    remove_argv,
    run_install as mcp_install,
)
from neosian._foundation.mcp.targets import SERVER_NAME, resolve_target as mcp_target
from neosian._foundation.memory.settings import CLIENT_TOKEN_ENV, StreamParser
from neosian._foundation.record.install import run_install as hooks_install
from neosian._foundation.shared.client_config import InstallError, add_level_argument

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from neosian._foundation.shared.client_config import Environment

    # The client's own CLI, run: (exit code, stderr), or None off PATH.
    Runner = Callable[[Sequence[str], Mapping[str, str]], tuple[int, str] | None]

CLIENTS: Final = ("claude-code", "codex", "opencode")  # both installers' rows
_DESCRIPTION: Final = "Wire the installed agents to this store: MCP and the hooks."
_EPILOG: Final = (
    "Print mode (the default) says what would land where; --write applies "
    "it. Once per machine by default: the registration and the hooks name "
    "the store and no mount, each session's layout is derived where it "
    "runs. `neosian mcp install` / `neosian record install` print the exact "
    "fragments and take every store flag."
)
_ONE_WRITER: Final = (
    "hint: one writer per FileStore root (DESIGN §8): the home is one root "
    "for every project on this machine; for more than one writer run "
    "`neosian serve` and re-run `neosian setup --url URL --write`"
)
_TOKEN: Final = (
    f"hint: set {CLIENT_TOKEN_ENV} in each client's own environment: the "
    "token is never written into a registration or a hook line"
)


def present_clients(context: Environment) -> list[str]:
    return [c for c in CLIENTS if mcp_target(c, context).evidence_dir.is_dir()]


def _spawn(argv: Sequence[str], env: Mapping[str, str]) -> tuple[int, str] | None:
    """Run the client's own CLI, no shell; None when it is not on PATH."""
    binary = shutil.which(argv[0], path=env.get("PATH", ""))
    if binary is None:
        return None
    try:
        done = subprocess.run(  # noqa: S603 - the argv is ours, the binary resolved
            [binary, *argv[1:]],
            env=dict(env),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return done.returncode, done.stderr.strip()


def _installer(
    run: Any,
    client: str,
    *,
    write: bool,
    extra: Sequence[str],
    env: Mapping[str, str],
    context: Environment,
) -> dict[str, Any]:
    out, err = io.StringIO(), io.StringIO()
    argv = ["--client", client, "--json", *extra, *(["--write"] if write else [])]
    code = run(argv, env, context=context, out=out, err=err)
    if code != 0 and not out.getvalue():
        return {"success": False, "error": err.getvalue().strip(), "hint": None}
    envelope: dict[str, Any] = json.loads(out.getvalue())
    if code == 2:  # the grammar tier's object carries the message as its hint
        return {"success": False, "error": envelope.get("hint"), "hint": None}
    envelope["applied"] = bool(envelope.get("written"))
    return envelope


def _apply(
    envelope: dict[str, Any], cli: str, *, runner: Runner, env: Mapping[str, str]
) -> None:
    """The client's own CLI writes its file: add, and over an entry that is
    already there, forget it and add again."""
    add = shlex.split(envelope["apply"])
    result = runner(add, env)
    if result is not None and result[0] != 0:
        runner(remove_argv(SERVER_NAME, cli), env)
        result = runner(add, env)
    if result is None:  # not on PATH: the line is the user's to run
        envelope.update(applied=False, apply_error=None)
    else:
        code, stderr = result
        envelope.update(
            applied=code == 0,
            apply_error=None if code == 0 else stderr or f"exit {code}",
        )


def _line(kind: str, envelope: dict[str, Any], *, write: bool) -> str:
    path = envelope.get("config_path", "-")
    if not envelope.get("success"):
        return f"  {kind:<6}{path}  refused: {envelope.get('error')}"
    if apply := envelope.get("apply"):
        if not write:
            return f"  {kind:<6}{path}  would run: {apply}"
        if envelope["applied"]:
            return f"  {kind:<6}{path}  applied: {apply}"
        why = envelope["apply_error"] or f"{shlex.split(apply)[0]} is not on PATH"
        return f"  {kind:<6}{path}  not applied ({why}); run: {apply}"
    if write:
        return (
            f"  {kind:<6}{path}  {'created' if envelope.get('created') else 'updated'}"
        )
    return f"  {kind:<6}{path}  would write"


def run_setup(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    context: Environment,
    out: TextIO,
    err: TextIO,
    prog: str = "neosian setup",
    runner: Runner = _spawn,
) -> int:
    parser = StreamParser(prog=prog, description=_DESCRIPTION, epilog=_EPILOG)
    parser.bind(out, err)
    parser.add_argument(
        "--client",
        action="append",
        choices=CLIENTS,
        default=[],
        help="only this client (repeatable; default: every client found)",
    )
    parser.add_argument(
        "--write", action="store_true", help="apply (default: print what would land)"
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output", help="one JSON object"
    )
    add_level_argument(parser)
    parser.add_argument("--root", help="the FileStore root both installers name")
    parser.add_argument(
        "--url", help=f"the state process both installers name ({CLIENT_TOKEN_ENV})"
    )
    try:
        args = parser.parse_args(list(argv))
    except SystemExit as exc:  # argparse: usage already on the streams
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 2
    clients = args.client or present_clients(context)
    if not clients:
        message = "no client found: none of " + ", ".join(CLIENTS) + " is installed"
        if args.json_output:
            out.write(json.dumps({"error": message, "hint": None}) + "\n")
        err.write(f"error: {message}\n")
        return 1
    extra = ["--level", args.level]
    extra += ["--root", args.root] if args.root else []
    extra += ["--url", args.url] if args.url else []
    rows: list[dict[str, Any]] = []
    for client in clients:
        target = mcp_target(client, context, args.level)
        # A file the client's CLI writes is rendered, then applied here.
        ours = target.cli is None or target.level != args.level
        mcp = _installer(
            mcp_install,
            client,
            write=args.write and ours,
            extra=extra,
            env=env,
            context=context,
        )
        if args.write and mcp.get("apply") and target.cli is not None:
            _apply(mcp, target.cli, runner=runner, env=context.env)
            shadow = mcp_target(client, context, "project")
            if mcp["applied"] and shadow.config_path != target.config_path:
                try:
                    displace(shadow, write=True)
                except InstallError as exc:
                    mcp["apply_error"] = exc.message
        hooks = _installer(
            hooks_install,
            client,
            write=args.write,
            extra=extra,
            env=env,
            context=context,
        )
        rows.append(
            {"client": client, "label": target.label, "mcp": mcp, "hooks": hooks}
        )
    failed = any(
        not row[k].get("success") or (args.write and not row[k]["applied"])
        for row in rows
        for k in ("mcp", "hooks")
    )
    if args.json_output:
        payload = {"written": args.write, "level": args.level, "clients": rows}
        out.write(json.dumps(payload) + "\n")
    else:
        for row in rows:
            out.write(f"{row['label']}\n")
            out.write(_line("mcp", row["mcp"], write=args.write) + "\n")
            out.write(_line("hooks", row["hooks"], write=args.write) + "\n")
        if not args.write:
            err.write("hint: re-run with --write to apply\n")
    err.write((_TOKEN if args.url else _ONE_WRITER) + "\n")
    return 1 if failed else 0
