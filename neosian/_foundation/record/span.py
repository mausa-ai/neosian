"""A foreign agent's span: hook payloads in, one turn's messages out
(DESIGN §20.9).

Pure — no I/O. What the spool keeps is the reduced record of each hook
call (the prompt, a tool round, the final text), never the payload; what
the store gets is the messages a Conversation would have written, in
provider order, so the audit view, the projector, recall and reflection
work on a foreign session unchanged (ledger #123). Transcript files are
never read. Payload fields verified against Claude Code's hooks
reference 2026-09-02: every event carries `session_id`; the prompt
event its text (`prompt`, `user_prompt` in the newer reference — both
read); `PostToolUse` the tool's name, input, `tool_use_id` and
response; `Stop` the `last_assistant_message`; a subagent's events an
`agent_id`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final

from neosian._foundation.llm.base import Message, Role, ToolCall
from neosian._foundation.shared.types import ToolCallId, ToolName

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

PROMPT_EVENT: Final = "UserPromptSubmit"
TOOL_EVENT: Final = "PostToolUse"
STOP_EVENT: Final = "Stop"
# The record, not the transcript: a tool's output is kept to its head.
TOOL_OUTPUT_CHARS: Final = 4096
PROMPT_LINE_CHARS: Final = 200
SESSIONS_DIR: Final = "sessions"

Record = dict[str, Any]


def parse_payload(text: str) -> dict[str, Any]:
    """One hook payload; the failure names what was wrong with it."""
    try:
        raw: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"stdin is not JSON: {exc}") from None
    if not isinstance(raw, dict):
        raise ValueError("stdin is not a JSON object")
    session = raw.get("session_id")
    if not isinstance(session, str) or not session:
        raise ValueError("the payload carries no session_id")
    payload: dict[str, Any] = raw
    return payload


def reduce_payload(payload: Mapping[str, Any]) -> tuple[str, Record | None]:
    """The spool record a payload becomes, with its disposition:
    `spooled` (a record), `skipped` (a subagent's round — its own
    session is not this span), `ignored` (an event the record does not
    track)."""
    event = payload.get("hook_event_name")
    if event == PROMPT_EVENT:
        text = payload.get("prompt") or payload.get("user_prompt") or ""
        return "spooled", {"kind": "prompt", "text": str(text)}
    if event == TOOL_EVENT:
        if payload.get("agent_id"):
            return "skipped", None
        call_id = payload.get("tool_use_id")
        tool_input = payload.get("tool_input")
        return "spooled", {
            "kind": "tool",
            "id": call_id if isinstance(call_id, str) and call_id else None,
            "name": str(payload.get("tool_name") or "tool"),
            "input": (
                tool_input if isinstance(tool_input, dict) else {"input": tool_input}
            ),
            "response": _render_response(payload.get("tool_response")),
        }
    if event == STOP_EVENT:
        text = payload.get("last_assistant_message") or ""
        return "spooled", {"kind": "stop", "text": str(text)}
    return "ignored", None


def _render_response(value: object) -> str:
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        text: str = value["text"]
    elif isinstance(value, str):
        text = value
    elif value is None:
        text = ""
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(text) <= TOOL_OUTPUT_CHARS:
        return text
    dropped = len(text) - TOOL_OUTPUT_CHARS
    return f"{text[:TOOL_OUTPUT_CHARS]}\n… [truncated {dropped} chars]"


def messages_of(records: Sequence[Record]) -> list[Message]:
    """The span as the messages a run would have produced, in order."""
    messages: list[Message] = []
    rounds = 0
    for record in records:
        kind = record.get("kind")
        if kind == "prompt":
            messages.append(Message(role=Role.USER, content=str(record["text"])))
        elif kind == "tool":
            rounds += 1
            call_id = ToolCallId(str(record.get("id") or f"round-{rounds}"))
            call = ToolCall(
                id=call_id,
                name=ToolName(str(record["name"])),
                arguments=record["input"],
            )
            messages.append(Message(role=Role.ASSISTANT, tool_calls=[call]))
            messages.append(
                Message(
                    role=Role.TOOL,
                    content=str(record["response"]),
                    tool_call_id=call_id,
                )
            )
        elif kind == "stop" and record.get("text"):
            messages.append(Message(role=Role.ASSISTANT, content=str(record["text"])))
    return messages


def last_prompt(records: Sequence[Record]) -> str | None:
    prompts = [str(r["text"]) for r in records if r.get("kind") == "prompt"]
    return prompts[-1] if prompts else None


def sessions_path(session_id: str) -> str:
    return f"{SESSIONS_DIR}/{session_id}"


def sessions_document(
    *,
    agent: str,
    session_id: str,
    started: datetime,
    last_prompt: str | None,
    turns: int,
) -> str:
    """The scope's per-session document — the listing the next agent
    finds in its index (memory documents, never a store method)."""
    first_line = (last_prompt or "").strip().splitlines()
    prompt = first_line[0][:PROMPT_LINE_CHARS] if first_line else "-"
    stamp = started.isoformat().replace("+00:00", "Z")
    return (
        f"# {agent} session {session_id}\n\n"
        f"- agent: {agent}\n"
        f"- conversation: {session_id}\n"
        f"- started: {stamp}\n"
        f"- last prompt: {prompt}\n"
        f"- turns: {turns}\n"
    )
