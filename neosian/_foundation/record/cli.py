"""`neosian record` — a foreign agent's hooks write and read the record
(DESIGN §20.9, §21.7).

One hook payload on stdin per call; `hook_event_name` says which. The
prompt and the tool rounds go to the spool; the stop lands the span as
one turn with the foreign actor `<agent>:<session_id>` in the
conversation the session id names, and writes the scope's sessions
document. `SessionStart` is the read side: the client injects a hook's
stdout as context, so that one event prints the memory index and "where
we left off" — every other event stays silent in text mode. Exit tiers
under a hook's semantics (Claude Code reads 2 as "block"): 2 only for
argv — nothing constructed — and 1 for everything after it (bad stdin,
an unreachable store, a corrupt spool), so a broken store never blocks
the agent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

import httpx

from neosian._foundation.conversation.base import ConversationStore
from neosian._foundation.conversation.ids import parse_conversation_id
from neosian._foundation.memory.actor import parse_actor
from neosian._foundation.memory.settings import StreamParser
from neosian._foundation.memory.store_lifetime import open_store
from neosian._foundation.record.context import (
    SESSION_START_EVENT,
    render_session_start,
)
from neosian._foundation.record.settings import (
    JSON_STOP_AGENTS,
    RecordSettings,
    add_record_arguments,
    resolve_record_settings,
)
from neosian._foundation.record.span import (
    STOP_EVENT,
    last_prompt,
    messages_of,
    parse_payload,
    reduce_payload,
    sessions_document,
    sessions_path,
)
from neosian._foundation.record.spool import Spool
from neosian._foundation.shared.exceptions import MemoryStoreError, NeosianError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from neosian._foundation.memory.base import MemoryStore

_DESCRIPTION = (
    "Record a foreign agent's session from its hooks — one payload on stdin per call."
)
_EPILOG = (
    "UserPromptSubmit opens a span, PostToolUse adds a tool round, Stop lands "
    "it as one turn by <agent>:<session_id> in the conversation the session "
    "id names, plus the scope's sessions document; SessionStart prints the "
    "memory index and where we left off (the client injects it as context). "
    "`neosian record install --client claude-code` renders the hooks; the "
    "store flags are the memory grammar's (hooks beside an MCP server are two "
    "writers — use --url or Postgres)."
)
_HINT = (
    "the record never blocks the agent — fix the cause and the next Stop "
    "lands the whole span (the spool keeps it)"
)


async def run(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    stdin: TextIO,
    out: TextIO,
    err: TextIO,
    prog: str = "neosian record",
) -> int:
    parser = StreamParser(prog=prog, description=_DESCRIPTION, epilog=_EPILOG)
    parser.bind(out, err)
    add_record_arguments(parser)
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="one JSON envelope on stdout (text mode prints nothing there, "
        "except SessionStart's context)",
    )
    try:
        args = parser.parse_args(list(argv))
        settings = resolve_record_settings(parser, args, env, layout=Path.cwd())
    except SystemExit as exc:  # argparse: usage already on the streams
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 2
    except MemoryStoreError as exc:  # Mount() scope/path validation
        err.write(f"error: [{exc.code}] {exc.message}\n")
        return 2
    try:
        envelope = await record_payload(settings, stdin.read())
    except (NeosianError, httpx.HTTPError, OSError, ValueError) as exc:
        message = getattr(exc, "message", None) or str(exc)
        code = getattr(exc, "code", None)
        text = f"[{code}] {message}" if code else message
        if args.json_output:
            out.write(json.dumps({"error": text, "hint": _HINT}) + "\n")
        err.write(f"error: {text}\nhint: {_HINT}\n")
        return 1
    if args.json_output:
        out.write(json.dumps(envelope) + "\n")
    elif envelope["context"] is not None:
        out.write(envelope["context"] + "\n")  # SessionStart: stdout is the context
    elif envelope["event"] == STOP_EVENT and settings.agent in JSON_STOP_AGENTS:
        out.write("{}\n")  # the client wants a JSON decision; this is none
    return 0


def _conversations(store: MemoryStore) -> ConversationStore:
    if not isinstance(
        store, ConversationStore
    ):  # pragma: no cover - shipped stores are
        raise ValueError("the store keeps no conversations")
    return store


async def record_payload(settings: RecordSettings, text: str) -> dict[str, Any]:
    """One payload in, the envelope out (the harness replays through here)."""
    payload = parse_payload(text)
    session_id = parse_conversation_id(payload["session_id"])
    actor = parse_actor(f"{settings.agent}:{session_id}")
    event = str(payload.get("hook_event_name"))
    disposition, record = reduce_payload(payload)
    envelope: dict[str, Any] = {
        "event": event,
        "session_id": session_id,
        "actor": actor,
        "disposition": disposition,
        "conversation_id": None,
        "turn": None,
        "document": None,
        "client": None,
        "context": None,
    }
    if event == SESSION_START_EVENT:
        async with open_store(settings.store) as store:
            envelope.update(
                disposition="context",
                client=getattr(store, "client", None),
                context=await render_session_start(
                    store,
                    _conversations(store),
                    settings,
                    session_id=session_id,
                    source=str(payload.get("source") or ""),
                ),
            )
        return envelope
    if record is None:
        return envelope
    spool = Spool(settings.spool)
    spool.append(session_id, record)  # spool first: a failed landing loses nothing
    if event != STOP_EVENT:
        return envelope
    records = spool.read(session_id)
    messages = messages_of(records)
    if not messages:
        spool.clear(session_id)
        envelope["disposition"] = "empty"
        return envelope
    async with open_store(settings.store) as memory:
        turns = _conversations(memory)
        turn = await turns.append_turn(session_id, messages, actor=actor)
        document = await memory.write(
            settings.mount.scope,
            sessions_path(session_id),
            sessions_document(
                agent=settings.agent,
                session_id=session_id,
                started=(await turns.read_turns(session_id, limit=1))[0].created_at,
                last_prompt=last_prompt(records),
                turns=turn.turn,
            ),
            actor=f"{actor}#{turn.turn}",
        )
        envelope.update(
            disposition="recorded",
            conversation_id=turn.conversation_id,
            turn=turn.turn,
            document=f"/{settings.mount.mount_path}/{document.path}",
            client=getattr(memory, "client", None),
        )
    spool.clear(session_id)
    return envelope
