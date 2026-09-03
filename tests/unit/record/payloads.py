"""Replayed Claude Code hook payloads — the shapes the hooks reference
documents (verified 2026-09-02, DESIGN §20.9): every event carries
`session_id`/`cwd`/`hook_event_name`/`transcript_path`; the prompt
event its text; `PostToolUse` the tool name, input, `tool_use_id` and
response; `Stop` the `last_assistant_message`."""

from __future__ import annotations

from typing import Any

SESSION = "3f2c9d1e-5a7b-4c8d-9e0f-112233445566"
_COMMON = {
    "transcript_path": "/home/u/.claude/projects/p/transcript.jsonl",
    "cwd": "/home/u/proj",
    "permission_mode": "default",
}


def prompt(
    text: str = "Add a test for login", *, session: str = SESSION
) -> dict[str, Any]:
    return {
        **_COMMON,
        "session_id": session,
        "hook_event_name": "UserPromptSubmit",
        "prompt": text,
    }


def tool(
    name: str = "Read",
    *,
    tool_input: dict[str, Any] | None = None,
    response: Any = None,
    tool_use_id: str | None = "toolu_01ABC",
    session: str = SESSION,
    agent_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        **_COMMON,
        "session_id": session,
        "hook_event_name": "PostToolUse",
        "tool_name": name,
        "tool_input": (
            {"file_path": "/home/u/proj/app.py"} if tool_input is None else tool_input
        ),
        "tool_response": (
            {"type": "text", "text": "def login(): ..."}
            if response is None
            else response
        ),
    }
    if tool_use_id is not None:
        payload["tool_use_id"] = tool_use_id
    if agent_id is not None:
        payload["agent_id"] = agent_id
    return payload


def stop(
    text: str = "Done — the test is in.", *, session: str = SESSION
) -> dict[str, Any]:
    return {
        **_COMMON,
        "session_id": session,
        "hook_event_name": "Stop",
        "stop_hook_active": False,
        "last_assistant_message": text,
    }


def session_start(source: str = "startup", *, session: str = SESSION) -> dict[str, Any]:
    """`source` is how the session began: startup, resume, clear, compact
    (both references), fork (Claude Code)."""
    return {
        **_COMMON,
        "session_id": session,
        "hook_event_name": "SessionStart",
        "source": source,
    }
