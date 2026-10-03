"""`neosian search` — history search from the shell (N5, DESIGN §32).

The store half of the grammar is shared (`--root` / `--url` / the DSN by
environment); there is no `--scope`, since turns carry none (§9.4): the
verb is store-wide, `--conversation` (repeatable) narrows, the host's
duty as ever (#295). The terms are the positional words, the rule the
store's own (§32: every term a substring, newest first). Exit tiers as
everywhere (§14.1): 2 for grammar — no term, a bad id, a limit outside
the wire's page — with nothing constructed; 1 when the store answers
with an error; 0 with the hits, or none (no hit is an answer).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final, TextIO

import httpx

from neosian._foundation.conversation.base import ConversationStore
from neosian._foundation.conversation.ids import parse_conversation_id
from neosian._foundation.conversation.search import parse_query
from neosian._foundation.memory.settings import (
    StoreSettings,
    StreamParser,
    add_store_selection_arguments,
    resolve_store_selection,
)
from neosian._foundation.memory.store_lifetime import open_store
from neosian._foundation.messaging.cli_history import search_hits
from neosian._foundation.shared.exceptions import (
    ConversationIdInvalidError,
    NeosianError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_DESCRIPTION = "Find the turns holding every term, newest first, in any conversation."
_EPILOG = (
    "Every substrate answers the same: a FileStore root, Postgres by "
    "NEOSIAN_POSTGRES_DSN, or the state process by --url (its token in "
    "NEOSIAN_CLIENT_TOKEN). A hit names the conversation and turn; "
    "`recall_turn` (in an agent) re-reads it verbatim."
)
DEFAULT_LIMIT: Final = 20
_LIMIT_MAX: Final = 500  # the wire's page (§18.2): every substrate alike


async def run(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    out: TextIO,
    err: TextIO,
    prog: str = "neosian search",
) -> int:
    parser = StreamParser(prog=prog, description=_DESCRIPTION, epilog=_EPILOG)
    parser.bind(out, err)
    parser.add_argument("query", nargs="+", help="the terms; a turn holds every one")
    add_store_selection_arguments(parser)
    parser.add_argument(
        "--conversation",
        action="append",
        dest="conversations",
        metavar="ID",
        help="search this conversation only (repeatable); default: every one",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"the newest N hits, 1 to {_LIMIT_MAX} (default: {DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output", help="one JSON object"
    )
    try:
        args = parser.parse_args(list(argv))
        selection = resolve_store_selection(parser, args, env)
        query = " ".join(args.query)
        try:
            terms = parse_query(query)
        except ValueError as exc:
            parser.error(str(exc))
        if not 1 <= args.limit <= _LIMIT_MAX:
            parser.error(f"--limit must be 1 to {_LIMIT_MAX}, got {args.limit}")
        for conversation in args.conversations or ():
            try:
                parse_conversation_id(conversation)
            except ConversationIdInvalidError as exc:
                parser.error(f"--conversation {conversation!r}: {exc.reason}")
    except SystemExit as exc:  # argparse: usage already on the streams
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 2
    settings = StoreSettings(
        mounts=(),
        root=selection.root,
        dsn=selection.dsn,
        schema=selection.schema,
        actor="cli:local",  # unused: a search writes nothing
        url=selection.url,
        client_token=selection.client_token,
    )
    try:
        async with open_store(settings) as store:
            if not isinstance(store, ConversationStore):  # pragma: no cover
                raise ValueError("the store keeps no conversations")
            turns = await store.search_turns(
                query, conversations=args.conversations, limit=args.limit
            )
            hits = await search_hits(
                store, turns, terms, args.conversations, args.limit
            )
            client = getattr(store, "client", None)
    except (NeosianError, httpx.HTTPError, OSError, ValueError) as exc:
        message = getattr(exc, "message", None) or str(exc)
        code = getattr(exc, "code", None)
        text = f"[{code}] {message}" if code else message
        if args.json_output:
            out.write(json.dumps({"error": text, "hint": None}) + "\n")
        err.write(f"error: {text}\n")
        return 1
    if args.json_output:
        envelope = {
            "query": query,
            "conversations": args.conversations,
            "limit": args.limit,
            "client": client,
            "hits": hits,
        }
        out.write(json.dumps(envelope) + "\n")
        return 0
    if not hits:
        out.write(f"no turn matches every term of {query!r}\n")
        return 0
    for hit in hits:
        if hit.get("type") == "message":
            out.write(f"{hit['annotation']}\n  {hit['snippet']}\n")
        else:
            out.write(
                f"[{hit['conversation_id']} #{hit['turn']}] {hit['created_at']} "
                f"{hit['actor'] or '-'}  {hit['snippet']}\n"
            )
            for note in hit.get("annotations", []):
                out.write(note + "\n")
    return 0
