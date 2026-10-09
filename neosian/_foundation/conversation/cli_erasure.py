"""`neosian redact` / `neosian prune`: the eraser from the shell (N8,
DESIGN §38), the `neosian export` shape: verb first, the store half of
the grammar shared (`--root` / `--url` / the DSN by environment, the
home when none is named).

`redact CONVERSATION` takes exactly one selector: `--all` (every turn,
the whole-conversation eraser, which also redacts the conversation's
`sessions/<id>` document wherever a scope holds one), `--through N` or
`--turns N[,N...]`; the irreversible whole takes the explicit token, the
`memory redact --all` precedent (ledger #104). `prune --older-than
<N>d|<N>h` redacts every conversation whose newest turn predates the
cutoff, and its sessions documents; a conversation already blanked whole
is left alone; `--dry-run` reports the same plan and writes nothing.
Exit tiers as everywhere (§14.1): 2 for grammar with nothing constructed
(no selector or two, a bad number or duration, an invalid id); 1 when
the store refuses (no `Erasable`, no `Portable`, a connection lost); 0
with the report, a count of zero included.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, TextIO

import httpx

from neosian._foundation.conversation.base import ConversationStore
from neosian._foundation.conversation.erasable import Erasable
from neosian._foundation.conversation.ids import parse_conversation_id
from neosian._foundation.memory.portable import Portable
from neosian._foundation.memory.sessions import SESSIONS_PREFIX, sessions_path
from neosian._foundation.memory.settings import (
    StoreSettings,
    StreamParser,
    add_store_selection_arguments,
    resolve_actor,
    resolve_store_selection,
)
from neosian._foundation.memory.store_lifetime import open_store
from neosian._foundation.shared.exceptions import (
    ConfigurationError,
    ConversationIdInvalidError,
    NeosianError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from neosian._foundation.memory.base import MemoryStore

VERBS = ("redact", "prune")
_DESCRIPTIONS = {
    "redact": "Blank recorded turns of one conversation, the skeleton kept: "
    "number, timestamp and actor stay, content goes. Irreversible.",
    "prune": "Redact every conversation whose newest turn is older than a "
    "cutoff, and its sessions documents. Irreversible; --dry-run first.",
}
_EPILOG = (
    "The store is a FileStore root (--root), Postgres by NEOSIAN_POSTGRES_DSN, "
    "the state process by --url (its token in NEOSIAN_CLIENT_TOKEN), or the "
    "home when none is named. The act lands in the trail under --actor; "
    "`neosian audit --conversation ID` shows it."
)
_DURATION = re.compile(r"\A([1-9]\d*)([dh])\Z")


def _duration(value: str) -> timedelta:
    match = _DURATION.match(value)
    if match is None:
        raise argparse.ArgumentTypeError(f"not a duration: {value!r} (e.g. 30d or 12h)")
    count = int(match.group(1))
    return timedelta(days=count) if match.group(2) == "d" else timedelta(hours=count)


def _turns(value: str) -> tuple[int, ...]:
    try:
        numbers = tuple(int(part) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"not turn numbers: {value!r} (e.g. 3 or 3,7)"
        ) from exc
    if min(numbers) < 1:
        raise argparse.ArgumentTypeError(f"turn numbers start at 1: {value!r}")
    return numbers


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
        err.write(f"usage: {prog} {{redact,prune}} ... [store flags] [--json]\n")
        return 2
    parser = StreamParser(
        prog=f"{prog} {verb}", description=_DESCRIPTIONS[verb], epilog=_EPILOG
    )
    parser.bind(out, err)
    if verb == "redact":
        parser.add_argument("conversation", metavar="CONVERSATION", help="the id")
        parser.add_argument(
            "--all",
            action="store_true",
            help="every turn: the whole conversation, its sessions documents too",
        )
        parser.add_argument("--through", type=int, metavar="N", help="turns 1 to N")
        parser.add_argument(
            "--turns", type=_turns, metavar="N[,N...]", help="these turn numbers"
        )
    else:
        parser.add_argument(
            "--older-than",
            type=_duration,
            required=True,
            metavar="<N>d|<N>h",
            help="the cutoff: conversations whose newest turn is older than this",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="report the plan; write nothing"
        )
    add_store_selection_arguments(parser)
    parser.add_argument(
        "--actor", default="cli:local", help="who erases (default: cli:local)"
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output", help="one JSON object"
    )
    conversation_id = ""
    try:
        args = parser.parse_args(list(argv[1:]))
        selection = resolve_store_selection(parser, args, env)
        actor = resolve_actor(parser, args.actor)
        if verb == "redact":
            try:
                conversation_id = parse_conversation_id(args.conversation)
            except ConversationIdInvalidError as exc:
                parser.error(f"CONVERSATION: {exc.reason}")
            chosen = sum(1 for given in (args.all, args.through, args.turns) if given)
            if chosen != 1:
                parser.error(
                    "give exactly one of --all, --through N or --turns N[,N...]"
                )
            if args.through is not None and args.through < 1:
                parser.error(f"--through must be >= 1, got {args.through}")
    except SystemExit as exc:  # argparse: usage already on the streams
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 2
    settings = StoreSettings(
        mounts=(),
        root=selection.root,
        dsn=selection.dsn,
        schema=selection.schema,
        actor=actor,
        url=selection.url,
        client_token=selection.client_token,
    )
    try:
        async with open_store(settings) as store:
            if not isinstance(store, Erasable):
                raise ConfigurationError(
                    f"{type(store).__name__} does not implement Erasable — its "
                    "turns cannot be redacted (DESIGN §38)"
                )
            if verb == "redact":
                envelope = await _redact(store, conversation_id, args, actor)
            else:
                cutoff = datetime.now(UTC) - args.older_than
                envelope = await _prune(store, cutoff, actor, dry_run=args.dry_run)
            envelope["client"] = getattr(store, "client", None)
    except (NeosianError, TypeError, httpx.HTTPError, OSError) as exc:
        message = getattr(exc, "message", None) or str(exc)
        code = getattr(exc, "code", None)
        text = f"[{code}] {message}" if code else message
        if args.json_output:
            out.write(json.dumps({"error": text, "hint": None}) + "\n")
        err.write(f"error: {text}\n")
        return 1
    if args.json_output:
        out.write(json.dumps(envelope) + "\n")
        return 0
    out.write(_render(envelope))
    return 0


async def _redact(
    store: MemoryStore, conversation_id: str, args: argparse.Namespace, actor: str
) -> dict[str, Any]:
    assert isinstance(store, Erasable)  # the caller refused otherwise
    count = await store.redact_turns(
        conversation_id, through=args.through, turns=args.turns, actor=actor
    )
    documents = (
        await _sessions(store, [conversation_id], actor, write=True) if args.all else []
    )
    return {
        "verb": "redact",
        "conversation_id": conversation_id,
        "count": count,
        "documents": documents,
    }


async def _prune(
    store: MemoryStore, cutoff: datetime, actor: str, *, dry_run: bool
) -> dict[str, Any]:
    assert isinstance(store, Erasable)  # the caller refused otherwise
    if not isinstance(store, Portable):
        raise ConfigurationError(
            f"{type(store).__name__} does not implement Portable — prune walks "
            "the whole store (DESIGN §26.1)"
        )
    assert isinstance(store, ConversationStore)  # every store `open_store` builds
    plan: list[dict[str, Any]] = []
    for conversation_id in await store.conversations():
        last = await store.last_turn_number(conversation_id)
        if last == 0:
            continue
        (newest,) = await store.read_turns(conversation_id, after=last - 1, limit=1)
        if newest.created_at >= cutoff:
            continue
        if all(turn.redacted for turn in await store.read_turns(conversation_id)):
            continue  # blanked whole already: nothing left to erase
        plan.append(
            {
                "conversation_id": conversation_id,
                "turns": last,
                "last_at": _stamp(newest.created_at),
            }
        )
    ids = [str(row["conversation_id"]) for row in plan]
    turns = sum(int(row["turns"]) for row in plan)
    if not dry_run:
        turns = sum([await store.redact_turns(name, actor=actor) for name in ids])
    documents = await _sessions(store, ids, actor, write=not dry_run)
    return {
        "verb": "prune",
        "cutoff": _stamp(cutoff),
        "dry_run": dry_run,
        "turns": turns,
        "conversations": plan,
        "documents": documents,
    }


async def _sessions(
    store: MemoryStore, ids: Sequence[str], actor: str, *, write: bool
) -> list[dict[str, str]]:
    """Every live `sessions/<id>` document a scope holds for the ids,
    redacted when `write`. Conversations carry no scope, so the scopes
    come from `Portable`; a store without it finds none."""
    if not ids or not isinstance(store, Portable):
        return []
    wanted = {sessions_path(name) for name in ids}
    found: list[dict[str, str]] = []
    for scope in await store.scopes():
        for entry in await store.list_documents(scope, prefix=SESSIONS_PREFIX):
            if entry.path in wanted and not entry.redacted:
                if write:
                    await store.redact(scope, path=entry.path, actor=actor)
                found.append({"scope": scope, "path": entry.path})
    return found


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _render(envelope: dict[str, Any]) -> str:
    did = "would redact" if envelope.get("dry_run") else "redacted"
    lines: list[str] = []
    if envelope["verb"] == "redact":
        count, name = envelope["count"], envelope["conversation_id"]
        lines.append(
            f"redacted {_n(count, 'turn')} of {name}"
            if count
            else f"no turns matched in {name}"
        )
    elif not envelope["conversations"]:
        lines.append(f"nothing older than {envelope['cutoff']}")
    else:
        lines.extend(
            f"{row['conversation_id']}  {_n(row['turns'], 'turn')}  last {row['last_at']}"
            for row in envelope["conversations"]
        )
        lines.append(
            f"{did} {_n(len(envelope['conversations']), 'conversation')}, "
            f"{_n(envelope['turns'], 'turn')} older than {envelope['cutoff']}"
        )
    lines.extend(
        f"{did} /{document['path']} in {document['scope']}"
        for document in envelope["documents"]
    )
    return "\n".join(lines) + "\n"


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")
