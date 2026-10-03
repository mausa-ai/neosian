"""`neosian continue` — the continue call from the shell (DESIGN §33).

What `continue_session` delivers, printed: the conversation named, else
the one a bare call would mean in a scope (the pending note's, else the
newest listed session). A read: a terminal is not a session, so the
note stays pending and no lineage is written — the agent's own call
does both. The store half of the grammar is shared (`--root` / `--url` /
the DSN by environment); `--scope` names the scope whose sessions and
note to read, this directory's project scope by default. Exit tiers as
everywhere (§14.1): 2 for grammar with nothing constructed; 1 when the
store answers with an error or there is nothing to continue; 0 with the
text.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

import httpx

from neosian._foundation.conversation.base import ConversationStore
from neosian._foundation.conversation.handoff import continue_conversation
from neosian._foundation.conversation.ids import parse_conversation_id
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
    ConversationIdInvalidError,
    MemoryScopeInvalidError,
    NeosianError,
)
from neosian._foundation.tools.base import ToolResult

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_DESCRIPTION = "Print a recorded conversation the way continue_session delivers it."
_EPILOG = (
    "A read: the handoff note stays pending and nothing is written — an "
    "agent's continue_session call picks the note up and records the "
    "lineage. Every substrate answers the same: a FileStore root, Postgres "
    "by NEOSIAN_POSTGRES_DSN, or the state process by --url."
)


async def run(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    out: TextIO,
    err: TextIO,
    prog: str = "neosian continue",
) -> int:
    parser = StreamParser(prog=prog, description=_DESCRIPTION, epilog=_EPILOG)
    parser.bind(out, err)
    parser.add_argument(
        "conversation",
        nargs="?",
        help="the conversation to deliver (default: the pending note's session, "
        "else the newest listed in the scope)",
    )
    add_store_selection_arguments(parser)
    parser.add_argument(
        "--scope",
        help=f"the scope whose sessions and note to read (default: ${SCOPE_ENV}, "
        "else this directory's project scope)",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output", help="one JSON object"
    )
    try:
        args = parser.parse_args(list(argv))
        selection = resolve_store_selection(parser, args, env)
        if args.conversation is not None:
            try:
                parse_conversation_id(args.conversation)
            except ConversationIdInvalidError as exc:
                parser.error(f"conversation {args.conversation!r}: {exc.reason}")
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
        actor="cli:local",  # unused: the shell's continue writes nothing
        url=selection.url,
        client_token=selection.client_token,
    )
    try:
        async with open_store(settings) as store:
            if not isinstance(store, ConversationStore):  # pragma: no cover
                raise ValueError("the store keeps no conversations")
            outcome = await continue_conversation(
                store, store, scope, args.conversation, pick_up=False
            )
            client = getattr(store, "client", None)
    except (NeosianError, httpx.HTTPError, OSError, ValueError) as exc:
        message = getattr(exc, "message", None) or str(exc)
        code = getattr(exc, "code", None)
        text = f"[{code}] {message}" if code else message
        return _failed(text, None, json_output=args.json_output, out=out, err=err)
    if isinstance(outcome, ToolResult):
        return _failed(
            str(outcome.error),
            outcome.system_reminder,
            json_output=args.json_output,
            out=out,
            err=err,
        )
    if args.json_output:
        envelope = {
            "conversation": outcome.conversation,
            "scope": scope,
            "note": outcome.note,
            "client": client,
            "text": outcome.text,
        }
        out.write(json.dumps(envelope) + "\n")
    else:
        out.write(outcome.text + "\n")
    return 0


def _resolve_scope(flag: str | None, env: Mapping[str, str]) -> str:
    """The flag, else `NEOSIAN_SCOPE`, else the working directory's project
    scope: the audit verb's rule, the mount the sessions land in."""
    named = flag if flag is not None else env.get(SCOPE_ENV) or None
    if named is not None:
        return str(parse_scope(named))
    return str(project_scope(Path.cwd()))


def _failed(
    text: str, hint: str | None, *, json_output: bool, out: TextIO, err: TextIO
) -> int:
    if json_output:
        out.write(json.dumps({"error": text, "hint": hint}) + "\n")
    err.write(f"error: {text}\n")
    if hint:
        err.write(f"hint: {hint}\n")
    return 1
