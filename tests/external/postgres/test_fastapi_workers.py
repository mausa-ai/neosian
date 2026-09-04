"""The application-level half of the N3 done-when: two web workers (two
apps, two stores, two pools) on one conversation behave correctly through
the FastAPI example, and the DESIGN §6 relay honors the wire contract —
keepalive comments and error frames are the host's, never the library's."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from examples.fastapi_chatbot import create_app
from neosian import (
    AgentConfig,
    Model,
    PostgresStore,
    Role,
    Tool,
    ToolCall,
    ToolResult,
)
from neosian._foundation.shared.types import ToolCallId, ToolName
from neosian.fake import FakeClient, FakeScript, FakeTurn
from tests.external.postgres.conftest import store_schema

_MESSAGES_URL = "/tenants/acme/users/ada/threads/general/messages"
_THREAD_URL = "/tenants/acme/users/ada/threads/general"
_CONVERSATION_ID = "acme--general"


@pytest.fixture
async def worker_b(
    postgres_dsn: str, store: PostgresStore
) -> AsyncIterator[PostgresStore]:
    """A second store on the same schema — a second worker process."""
    other = PostgresStore(
        postgres_dsn, schema=store_schema(store), min_size=1, max_size=4
    )
    try:
        yield other
    finally:
        await other.aclose()


def _agent_config(script: FakeScript, **kwargs: Any) -> AgentConfig:
    return AgentConfig(
        system_prompt="You are a test agent.",
        model=Model.FAKE,
        enable_todo=False,
        client_factory=lambda _: FakeClient(script),
        **kwargs,
    )


def _reply(text: str) -> FakeScript:
    return FakeScript(turns=(FakeTurn(content=text),))


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://worker"
    )


async def _send(client: httpx.AsyncClient, text: str) -> str:
    response = await client.post(_MESSAGES_URL, json={"message": text})
    assert response.status_code == 200
    return response.text


def _frames(body: str) -> list[tuple[str, dict[str, Any]]]:
    """Split an SSE body into (event name, payload) pairs, skipping the
    comment frames a keepalive emits."""
    frames: list[tuple[str, dict[str, Any]]] = []
    for block in body.split("\n\n"):
        if not block or block.startswith(":"):
            continue
        event_line, data_line = block.split("\n", 1)
        assert event_line.startswith("event: ")
        assert data_line.startswith("data: ")
        frames.append((event_line[len("event: ") :], json.loads(data_line[6:])))
    return frames


async def test_two_workers_append_gapless_turns_to_one_conversation(
    store: PostgresStore, worker_b: PostgresStore
) -> None:
    app_a = create_app(store=store, agent_config=_agent_config(_reply("ok")))
    app_b = create_app(store=worker_b, agent_config=_agent_config(_reply("ok")))
    async with _client(app_a) as a, _client(app_b) as b:
        bodies = await asyncio.gather(_send(a, "from-a"), _send(b, "from-b"))
    for body in bodies:
        assert "event: done" in body
    turns = await store.read_turns(_CONVERSATION_ID)
    assert [turn.turn for turn in turns] == [1, 2]
    users = {
        message.content
        for turn in turns
        for message in turn.messages
        if message.role is Role.USER
    }
    assert users == {"from-a", "from-b"}


async def test_a_fresh_conversation_resumes_both_workers_turns(
    store: PostgresStore, worker_b: PostgresStore
) -> None:
    app_a = create_app(store=store, agent_config=_agent_config(_reply("ok")))
    app_b = create_app(store=worker_b, agent_config=_agent_config(_reply("ok")))
    async with _client(app_a) as a, _client(app_b) as b:
        await asyncio.gather(_send(a, "from-a"), _send(b, "from-b"))
        history = await b.get(_THREAD_URL)
    assert history.status_code == 200
    data = history.json()
    assert data["conversation_id"] == _CONVERSATION_ID
    assert len(data["messages"]) == 4
    user_texts = {m["content"] for m in data["messages"] if m["role"] == "user"}
    assert user_texts == {"from-a", "from-b"}


async def test_relay_frames_carry_the_sse_wire_form(store: PostgresStore) -> None:
    app = create_app(store=store, agent_config=_agent_config(_reply("streamed")))
    async with _client(app) as client:
        body = await _send(client, "hi")
    frames = _frames(body)
    names = [name for name, _ in frames]
    assert names[0] == "ready"
    assert names[-1] == "done"
    for name, payload in frames:
        assert payload["event"] == name
    sequences = [payload["sequence"] for _, payload in frames]
    assert sequences[0] == 1
    assert all(b > a for a, b in zip(sequences, sequences[1:], strict=False))


async def test_keepalive_comments_appear_while_a_tool_runs(
    store: PostgresStore,
) -> None:
    """The regression pin for the relay's task discipline: a naive
    `wait_for(anext(...))` would cancel into the agent's generator on the
    first keepalive and the stream would never reach `done`."""

    @Tool(name="nap", description="Sleep briefly")
    async def nap() -> ToolResult[str]:
        await asyncio.sleep(0.2)
        return ToolResult.ok("rested")

    script = FakeScript(
        turns=(
            FakeTurn(
                tool_calls=(
                    ToolCall(id=ToolCallId("c1"), name=ToolName("nap"), arguments={}),
                )
            ),
            FakeTurn(content="done napping"),
        )
    )
    app = create_app(
        store=store,
        agent_config=_agent_config(script, tools=[nap]),
        keepalive_seconds=0.05,
    )
    async with _client(app) as client:
        body = await _send(client, "nap please")
    assert body.count(": keepalive") >= 1
    assert "event: done" in body


async def test_memory_write_frame_and_working_undo(store: PostgresStore) -> None:
    """The NP done-when: the host sees a memory write as a typed frame on
    the stream — after its tool_result, content-free — and the frame's
    `{path, version}` drives a working undo through the revert route."""
    script = FakeScript(
        turns=(
            FakeTurn(
                tool_calls=(
                    ToolCall(
                        id=ToolCallId("c1"),
                        name=ToolName("memory"),
                        arguments={
                            "command": "create",
                            "path": "/memories/prefs",
                            "content": "ada likes tea",
                        },
                    ),
                )
            ),
            FakeTurn(content="remembered"),
        )
    )
    app = create_app(store=store, agent_config=_agent_config(script))
    async with _client(app) as client:
        body = await _send(client, "remember: I like tea")
        frames = _frames(body)
        names = [name for name, _ in frames]
        write_at = names.index("memory_write")
        assert names[write_at - 1] == "tool_result"
        write = frames[write_at][1]
        assert write["command"] == "create"
        assert write["path"] == "/memories/prefs"
        assert write["version"] == 1
        assert "ada likes tea" not in json.dumps(write)  # never the content

        undo = await client.post(
            f"{_THREAD_URL}/memories/undo",
            json={"path": write["path"], "version": write["version"]},
        )
    assert undo.status_code == 200
    payload = undo.json()
    assert payload["success"] is True
    assert payload["version"] == 2  # the revert appended its own row
    assert await store.read("tenant:acme/user:ada", "prefs") is None
    rows = await store.versions("tenant:acme/user:ada", "prefs")
    assert [row.action for row in rows] == ["deleted", "created"]
    assert rows[0].actor == "acme--general#undo"


async def test_error_frame_carries_a_code_and_no_message(
    store: PostgresStore,
) -> None:
    script = FakeScript(turns=(FakeTurn(error=RuntimeError("boom")),))
    app = create_app(store=store, agent_config=_agent_config(script))
    async with _client(app) as client:
        body = await _send(client, "hello")
    name, payload = _frames(body)[-1]
    assert name == "error"
    assert payload["code"] == "llm_model_failed"
    assert "message" not in payload
    assert "boom" not in body
    assert await store.read_turns(_CONVERSATION_ID) == ()
