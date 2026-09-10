"""`neosian setup` — wire every installed client to this store (DESIGN
§30.3).

Detects the clients present on this machine (each installer target's
evidence directory), runs both installers for each — the MCP
registration and the record hooks, on the home and this directory's
layout — printing what would land by default and applying it with
`--write`; `--client` narrows; `--json` one object with both envelopes
per client; the one-writer hint once. Exit 0, or 1 when any installer
refused (every client is still reported).
"""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING, Any, Final, TextIO

from neosian._foundation.mcp.install import (
    resolve_target as mcp_target,
    run_install as mcp_install,
)
from neosian._foundation.memory.settings import StreamParser
from neosian._foundation.record.install import run_install as hooks_install

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from neosian._foundation.shared.client_config import Environment

CLIENTS: Final = ("claude-code", "codex", "opencode")  # both installers' rows
_DESCRIPTION: Final = "Wire the installed agents to this store: MCP and the hooks."
_EPILOG: Final = (
    "Print mode (the default) says what would land where; --write applies "
    "it. The registration and the hooks name the home and this directory's "
    "layout — `neosian mcp install` / `neosian record install` print the "
    "exact fragments and take the store flags."
)
_ONE_WRITER: Final = (
    "hint: one writer per FileStore root (DESIGN §8) — the hooks and the MCP "
    "server of every client share the home; for more than one writer run "
    "`neosian serve` and install with --url"
)


def present_clients(context: Environment) -> list[str]:
    return [c for c in CLIENTS if mcp_target(c, context).evidence_dir.is_dir()]


def _installer(
    run: Any, client: str, *, write: bool, env: Mapping[str, str], context: Environment
) -> dict[str, Any]:
    out, err = io.StringIO(), io.StringIO()
    argv = ["--client", client, "--json", *(["--write"] if write else [])]
    code = run(argv, env, context=context, out=out, err=err)
    if code != 0 and not out.getvalue():
        return {"success": False, "error": err.getvalue().strip(), "hint": None}
    envelope: dict[str, Any] = json.loads(out.getvalue())
    return envelope


def _line(kind: str, envelope: dict[str, Any], *, write: bool) -> str:
    path = envelope.get("config_path", "-")
    if not envelope.get("success"):
        return f"  {kind:<6}{path}  refused: {envelope.get('error')}"
    if envelope.get("apply"):
        return f"  {kind:<6}{path}  print-only: {envelope['apply']}"
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
    rows: list[dict[str, Any]] = []
    for client in clients:
        rows.append(
            {
                "client": client,
                "label": mcp_target(client, context).label,
                "mcp": _installer(
                    mcp_install, client, write=args.write, env=env, context=context
                ),
                "hooks": _installer(
                    hooks_install, client, write=args.write, env=env, context=context
                ),
            }
        )
    failed = any(not row[k].get("success") for row in rows for k in ("mcp", "hooks"))
    if args.json_output:
        out.write(json.dumps({"written": args.write, "clients": rows}) + "\n")
    else:
        for row in rows:
            out.write(f"{row['label']}\n")
            out.write(_line("mcp", row["mcp"], write=args.write) + "\n")
            out.write(_line("hooks", row["hooks"], write=args.write) + "\n")
        if not args.write:
            err.write("hint: re-run with --write to apply\n")
    err.write(_ONE_WRITER + "\n")
    return 1 if failed else 0
