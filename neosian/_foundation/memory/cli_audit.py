"""`neosian audit` — the ledger from the shell (NL, DESIGN §20).

The store half of the grammar is shared (`--root` / `--url` / the DSN by
environment); `--scope` here is a raw scope, never a mount — defaulting
like every shell verb's (§30: `NEOSIAN_SCOPE`, else this directory's
project scope) — and `--actor` is a *filter* (the audit writes nothing). Exit tiers as
everywhere (§14.1): 2 for grammar — an unparseable `--since`, a naive
one, an invalid scope — with nothing constructed; 1 when the store
answers with an error; 0 with the entries, or none (an empty ledger is
an answer).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

import httpx

from neosian._foundation.memory.audit import AuditEntry, audit
from neosian._foundation.memory.home import project_scope
from neosian._foundation.memory.scope import parse_scope
from neosian._foundation.memory.settings import (
    SCOPE_ENV,
    StoreSettings,
    StreamParser,
    add_store_selection_arguments,
    resolve_store_selection,
)
from neosian._foundation.memory.store_lifetime import open_store
from neosian._foundation.shared.exceptions import (
    ConfigurationError,
    MemoryScopeInvalidError,
    NeosianError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_DESCRIPTION = "What was done, by whom, when — a scope's ledger, newest first."
_EPILOG = (
    "Every substrate answers the same: a FileStore root, Postgres by "
    "NEOSIAN_POSTGRES_DSN, or the state process by --url (its token in "
    "NEOSIAN_CLIENT_TOKEN). --actor filters by prefix: `claude-code:s1` "
    "matches `claude-code:s1#4` and `claude-code:s1/conv:x#2`."
)


def _timestamp(value: str) -> datetime:
    """`--since`: ISO-8601, tz-aware (C4); argparse renders the failure."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"not an ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{value!r} is naive — give an offset (…Z or …+02:00)")
    return parsed


async def run(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    out: TextIO,
    err: TextIO,
    prog: str = "neosian audit",
) -> int:
    parser = StreamParser(prog=prog, description=_DESCRIPTION, epilog=_EPILOG)
    parser.bind(out, err)
    add_store_selection_arguments(parser)
    parser.add_argument(
        "--scope",
        help=f"the scope to audit (default: ${SCOPE_ENV}, else this directory's "
        "project scope)",
    )
    parser.add_argument("--conversation", help="merge one conversation's turns in")
    parser.add_argument("--actor", help="only this actor and what it wrote under it")
    parser.add_argument(
        "--since", type=_timestamp, help="ISO-8601 with an offset; inclusive"
    )
    parser.add_argument("--limit", type=int, help="the newest N entries")
    parser.add_argument(
        "--json", action="store_true", dest="json_output", help="one JSON object"
    )
    try:
        args = parser.parse_args(list(argv))
        selection = resolve_store_selection(parser, args, env)
        if args.limit is not None and args.limit < 0:
            parser.error(f"--limit must be >= 0, got {args.limit}")
        try:
            scope = _resolve_scope(args.scope, env)
        except MemoryScopeInvalidError as exc:
            parser.error(f"--scope: {exc.reason}")
        except ConfigurationError as exc:
            parser.error(exc.message)
    except SystemExit as exc:  # argparse: usage already on the streams
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 2
    settings = StoreSettings(
        mounts=(),
        root=selection.root,
        dsn=selection.dsn,
        schema=selection.schema,
        actor="cli:local",  # unused: the audit writes nothing
        url=selection.url,
        client_token=selection.client_token,
    )
    try:
        async with open_store(settings) as store:
            entries = await audit(
                store,
                scope,
                conversation_id=args.conversation,
                actor=args.actor,
                since=args.since,
                limit=args.limit,
            )
            client = getattr(store, "client", None)
    except (NeosianError, httpx.HTTPError, OSError) as exc:
        message = getattr(exc, "message", None) or str(exc)
        code = getattr(exc, "code", None)
        text = f"[{code}] {message}" if code else message
        if args.json_output:
            out.write(json.dumps({"error": text, "hint": None}) + "\n")
        err.write(f"error: {text}\n")
        return 1
    if args.json_output:
        envelope = {
            "scope": scope,
            "conversation_id": args.conversation,
            "client": client,
            "entries": [_to_json(entry) for entry in entries],
        }
        out.write(json.dumps(envelope) + "\n")
        return 0
    if not entries:
        out.write(f"no ledger entries for scope {scope!r}\n")
        return 0
    for entry in entries:
        out.write(_line(entry) + "\n")
    return 0


def _resolve_scope(flag: str | None, env: Mapping[str, str]) -> str:
    """The flag, else `NEOSIAN_SCOPE`, else the working directory's project
    scope (§30) — the mount the sessions document lands in."""
    named = flag if flag is not None else env.get(SCOPE_ENV) or None
    if named is not None:
        return str(parse_scope(named))
    return str(project_scope(Path.cwd()))


def _to_json(entry: AuditEntry) -> dict[str, Any]:
    return {
        "created_at": entry.created_at.isoformat().replace("+00:00", "Z"),
        "actor": entry.actor,
        "event": entry.event,
        "path": entry.path,
        "version": entry.version,
        "redacted": entry.redacted,
        "count": entry.count,
        "conversation_id": entry.conversation_id,
        "turn": entry.turn,
    }


def _line(entry: AuditEntry) -> str:
    stamp = entry.created_at.isoformat().replace("+00:00", "Z")
    actor = entry.actor or "-"
    if entry.event == "turn":
        target = f"{entry.conversation_id} turn {entry.turn}"
    elif entry.event == "redacted":
        where = "scope-wide" if entry.path is None else f"/{entry.path}"
        target = f"{entry.count} at {where}"
    else:
        marker = " (redacted)" if entry.redacted else ""
        target = f"/{entry.path} v{entry.version}{marker}"
    return f"{stamp}  {actor}  {entry.event}  {target}"
