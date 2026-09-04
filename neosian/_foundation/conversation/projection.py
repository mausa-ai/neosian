"""Log-projection rendering (DESIGN §9.6) — pure functions, no I/O.

The view is a pure function over ``(turns, projections)``: a turn covered
by a projection renders as its winning entry's line inside a log block;
an uncovered turn renders verbatim. Coverage alone decides — the hot band
is a boundary-time writer invariant, never a filter here (ledger #27), so
a resumed conversation renders identically under config drift.

Role labels are full words (USER, AGENT, TOOL). Entries are one line
each; the log block travels as a single synthetic USER message — never
SYSTEM, which the Anthropic adapter would treat as the system prompt.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

from neosian._foundation.conversation.links import LinkRegistry, boundary
from neosian._foundation.conversation.types import (
    ConversationProjection,
    ConversationTurn,
)
from neosian._foundation.llm.base import Message, Role, text_of
from neosian._foundation.shared.prompt_assets import get_prompt

if TYPE_CHECKING:
    from collections.abc import Sequence

# Tool segments of a log line: the args digest and the result head/tail
# are bounded independently of digest_chars — they exist to identify the
# round, not to carry it (recall_turn carries it).
_ARGS_CHARS: Final = 80
_RESULT_HEAD: Final = 100
_RESULT_TAIL: Final = 40
_SEPARATOR: Final = " | "


def one_line(
    text: str, limit: int, *, links: LinkRegistry | None = None, marker: str = " …"
) -> str:
    """Whitespace-flattened, contracted when `links` is given, head-clipped
    at an atom boundary — a clip never splits a URL, path, id or handle
    (§23); a token that cannot fit is dropped whole behind the marker."""
    flat = " ".join(text.split())
    if links is not None:
        flat = links.contract(flat)
    if len(flat) <= limit:
        return flat
    return (flat[: boundary(flat, limit)[0]].rstrip() + marker).lstrip()


def select(entries: Sequence[ConversationProjection]) -> dict[int, int]:
    """Winning entry index per covered turn: widest span, ties to later.

    Entries arrive in store order — (turn, span, insertion) — so "later
    index" realizes §9.6's tie-to-the-last-appended whenever the tie is
    real (equal turn and equal span). A naive last-write-wins overwrite
    would let a narrower later entry beat a wider fold; the explicit
    (span, index) max cannot.
    """
    best: dict[int, tuple[int, int]] = {}
    for index, entry in enumerate(entries):
        for turn in range(entry.turn - entry.span + 1, entry.turn + 1):
            key = (entry.span, index)
            if turn not in best or key >= best[turn]:
                best[turn] = key
    return {turn: index for turn, (_, index) in best.items()}


def entry_line(entry: ConversationProjection) -> str:
    """The entry's log line with its turn-ref label: ``[7]`` / ``[3-6]``."""
    if entry.span == 1:
        return f"[{entry.turn}] {entry.text}"
    return f"[{entry.turn - entry.span + 1}-{entry.turn}] {entry.text}"


def render_view(
    turns: Sequence[ConversationTurn],
    entries: Sequence[ConversationProjection],
) -> list[Message]:
    """The context-window view: projected turns as log blocks, the rest
    verbatim. With zero entries this is the flat history, message-identical.

    Whole turns only — a turn is a self-contained replayable unit
    (§9.5.1–2), so the view can never orphan a TOOL message.
    """
    winner = select(entries)
    out: list[Message] = []
    block: list[str] = []
    emitted: set[int] = set()
    for turn in turns:
        index = winner.get(turn.turn)
        if index is None:
            if block:
                out.append(_log_message(block))
                block = []
            out.extend(turn.messages)
        elif index not in emitted:
            emitted.add(index)
            block.append(entry_line(entries[index]))
    if block:
        out.append(_log_message(block))
    return out


def _log_message(lines: Sequence[str]) -> Message:
    body = "\n".join(lines)
    header = get_prompt("compaction.log_header")
    footer = get_prompt("compaction.log_footer")
    return Message(role=Role.USER, content=f"{header}\n{body}\n{footer}")


def agent_prose(turn: ConversationTurn) -> str:
    """Every ASSISTANT text segment of the turn, in provider order."""
    parts = [
        text_of(message).strip()
        for message in turn.messages
        if message.role is Role.ASSISTANT and text_of(message).strip()
    ]
    return "\n".join(parts)


def needs_distillation(
    turn: ConversationTurn, *, digest_chars: int, links: LinkRegistry
) -> bool:
    """Long assistant prose wants the model; user/tool length never does.
    Measured contracted — a reply that is mostly one URL needs no call."""
    return len(links.contract(agent_prose(turn))) > digest_chars


def log_line(
    turn: ConversationTurn,
    *,
    digest_chars: int,
    user_chars: int,
    links: LinkRegistry,
    agent_override: str | None = None,
) -> str:
    """One deterministic log line for a turn, in provider order.

    USER text survives verbatim up to `user_chars`, then head-clips with
    an inline recall pointer (ledger #31). AGENT prose clips at
    `digest_chars` unless `agent_override` — the model-distilled digest —
    replaces the turn's combined prose as a single segment. Tool rounds
    render ``TOOL name(args digest) → result head/tail``. Every segment
    is contracted through `links` and clipped atom-safe (§23).
    """
    results = {
        message.tool_call_id: message
        for message in turn.messages
        if message.role is Role.TOOL
    }
    segments: list[str] = []
    override_spliced = False
    for message in turn.messages:
        if message.role is Role.USER:
            text = one_line(
                text_of(message),
                user_chars,
                links=links,
                marker=f" … [recall_turn({turn.turn})]",
            )
            segments.append("USER: " + text)
        elif message.role is Role.ASSISTANT:
            prose = text_of(message).strip()
            if prose:
                if agent_override is None:
                    segments.append(
                        "AGENT: " + one_line(prose, digest_chars, links=links)
                    )
                elif not override_spliced:
                    segments.append(
                        "AGENT: " + one_line(agent_override, digest_chars, links=links)
                    )
                    override_spliced = True
            for call in message.tool_calls:
                segments.append(
                    _tool_segment(
                        call.name, call.arguments, results.get(call.id), links
                    )
                )
    return _SEPARATOR.join(segments)


def _tool_segment(
    name: str,
    arguments: dict[str, object],
    result: Message | None,
    links: LinkRegistry,
) -> str:
    args = one_line(
        json.dumps(arguments, separators=(",", ":")), _ARGS_CHARS, links=links
    )
    outcome = "?" if result is None else _result_digest(text_of(result), links)
    return f"TOOL {name}({args}) → {outcome}"


def _result_digest(raw: str, links: LinkRegistry) -> str:
    try:
        payload = json.loads(raw)
    except ValueError:
        return _head_tail(raw, links)
    if isinstance(payload, dict):
        if payload.get("success") is False:
            return _head_tail(f"error: {payload.get('error')}", links)
        if "data" in payload:
            return _head_tail(str(payload["data"]), links)
    return _head_tail(raw, links)


def _head_tail(text: str, links: LinkRegistry) -> str:
    """Head and tail, each cut moved off any atom it lands in: the head
    ends before it, the tail starts after it — dropped whole, never split."""
    flat = links.contract(" ".join(text.split()))
    if len(flat) <= _RESULT_HEAD + _RESULT_TAIL:
        return flat
    head = boundary(flat, _RESULT_HEAD)[0]
    tail = boundary(flat, len(flat) - _RESULT_TAIL)[1]
    return flat[:head].rstrip() + " … " + flat[tail:].lstrip()


def render_turn(turn: ConversationTurn) -> str:
    """The verbatim role-labeled turn for recall_turn — full text, no
    clipping (reasoning is model-internal and omitted)."""
    names = {
        call.id: call.name for message in turn.messages for call in message.tool_calls
    }
    lines = [f"Turn {turn.turn} (verbatim):"]
    for message in turn.messages:
        if message.role is Role.USER:
            lines.append(f"USER: {text_of(message)}")
        elif message.role is Role.ASSISTANT:
            prose = text_of(message).strip()
            if prose:
                lines.append(f"AGENT: {prose}")
            for call in message.tool_calls:
                args = json.dumps(call.arguments, separators=(",", ":"))
                lines.append(f"AGENT calls {call.name}({args})")
        elif message.role is Role.TOOL:
            call_id = message.tool_call_id
            name = names.get(call_id, "tool") if call_id is not None else "tool"
            lines.append(f"TOOL {name} → {text_of(message)}")
    return "\n".join(lines)
