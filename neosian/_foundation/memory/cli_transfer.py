"""`neosian export DIR` / `neosian import DIR` — a store moves whole
(NC4, DESIGN §26.4), the `neosian audit` shape.

The store half of the grammar is shared (`--root` / `--url` / the DSN by
environment, the home when none is named); `DIR` is the archive — a
FileStore root, so `cat`, `grep`, `serve` and `FileStore(DIR)` all read
it. `--scope` / `--conversation` narrow to named units (naming either
moves only what is named). Exit tiers as everywhere (§14.1): 2 for
grammar with nothing constructed — an unknown verb, a bad name, an
import of a directory that is not there; 1 when a store refuses (an
occupied unit, a backend without the protocol, a connection lost); 0
with the report, or with nothing to move.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

import httpx

from neosian._foundation.conversation.ids import parse_conversation_id
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.scope import parse_scope
from neosian._foundation.memory.settings import (
    StoreSettings,
    StreamParser,
    add_store_selection_arguments,
    resolve_store_selection,
)
from neosian._foundation.memory.store_lifetime import open_store
from neosian._foundation.memory.transfer import transfer
from neosian._foundation.shared.exceptions import (
    ConversationIdInvalidError,
    MemoryScopeInvalidError,
    NeosianError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from neosian._foundation.memory.portable import TransferReport, UnitReport

VERBS = ("export", "import")
_DESCRIPTIONS = {
    "export": "Write the store to DIR, whole — every scope and conversation, "
    "history included. DIR is a FileStore root you can read, serve or import.",
    "import": "Restore DIR — an export — into the store, verbatim. Every unit "
    "(a scope, a conversation) must be empty in the store; nothing merges.",
}
_EPILOG = (
    "The store is a FileStore root (--root), Postgres by NEOSIAN_POSTGRES_DSN, "
    "the state process by --url (its token in NEOSIAN_CLIENT_TOKEN), or the "
    "home when none is named. --scope / --conversation move only what they "
    "name; either alone moves nothing of the other kind."
)


async def run(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    out: TextIO,
    err: TextIO,
    prog: str = "neosian",
) -> int:
    verb = argv[0] if argv else ""
    if verb not in VERBS:
        err.write(f"usage: {prog} {{export,import}} DIR [store flags] [--json]\n")
        return 2
    parser = StreamParser(
        prog=f"{prog} {verb}", description=_DESCRIPTIONS[verb], epilog=_EPILOG
    )
    parser.bind(out, err)
    parser.add_argument("directory", metavar="DIR", help="the archive directory")
    add_store_selection_arguments(parser)
    parser.add_argument("--scope", action="append", help="only this scope (repeatable)")
    parser.add_argument(
        "--conversation", action="append", help="only this conversation (repeatable)"
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output", help="one JSON object"
    )
    try:
        args = parser.parse_args(list(argv[1:]))
        selection = resolve_store_selection(parser, args, env)
        for scope in args.scope or ():
            try:
                parse_scope(scope)
            except MemoryScopeInvalidError as exc:
                parser.error(f"--scope: {exc.reason}")
        for conversation_id in args.conversation or ():
            try:
                parse_conversation_id(conversation_id)
            except ConversationIdInvalidError as exc:
                parser.error(f"--conversation: {exc.reason}")
        directory = Path(args.directory)
        if verb == "import" and not directory.is_dir():
            parser.error(f"DIR: {directory} is not a directory")
    except SystemExit as exc:  # argparse: usage already on the streams
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 2
    settings = StoreSettings(
        mounts=(),
        root=selection.root,
        dsn=selection.dsn,
        schema=selection.schema,
        actor="cli:local",  # unused: restores carry the archive's actors
        url=selection.url,
        client_token=selection.client_token,
    )
    try:
        async with open_store(settings) as store:
            archive = FileStore(directory)
            source, target = (store, archive) if verb == "export" else (archive, store)
            report = await transfer(
                source, target, scopes=args.scope, conversations=args.conversation
            )
            client = getattr(store, "client", None)
    except (NeosianError, TypeError, httpx.HTTPError, OSError) as exc:
        message = getattr(exc, "message", None) or str(exc)
        code = getattr(exc, "code", None)
        text = f"[{code}] {message}" if code else message
        if args.json_output:
            out.write(json.dumps({"error": text, "hint": None}) + "\n")
        err.write(f"error: {text}\n")
        return 1
    if args.json_output:
        envelope = {
            "verb": verb,
            "archive": str(directory.resolve()),
            "client": client,
            "units": [_to_json(unit) for unit in report.units],
        }
        out.write(json.dumps(envelope) + "\n")
        return 0
    out.write(_render(verb, directory, report))
    return 0


def _to_json(unit: UnitReport) -> dict[str, Any]:
    return {
        "kind": unit.kind,
        "name": unit.name,
        "documents": unit.documents,
        "versions": unit.versions,
        "redactions": unit.redactions,
        "turns": unit.turns,
        "projections": unit.projections,
    }


def _render(verb: str, directory: Path, report: TransferReport) -> str:
    if not report.units:
        return f"nothing to {verb}\n"
    lines = [_line(unit) for unit in report.units]
    scopes = sum(unit.kind == "scope" for unit in report.units)
    conversations = len(report.units) - scopes
    where = "to" if verb == "export" else "from"
    lines.append(
        f"{verb}ed {_n(scopes, 'scope')}, {_n(conversations, 'conversation')} "
        f"{where} {directory}"
    )
    return "\n".join(lines) + "\n"


def _line(unit: UnitReport) -> str:
    counts: tuple[str, ...]
    if unit.kind == "scope":
        counts = (
            _n(unit.documents, "document"),
            _n(unit.versions, "version"),
            _n(unit.redactions, "redaction"),
        )
    else:
        counts = (_n(unit.turns, "turn"), _n(unit.projections, "projection"))
    return f"{unit.kind:<12} {unit.name}  " + "  ".join(counts)


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")
