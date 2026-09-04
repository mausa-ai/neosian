"""The NK done-when, pinned keylessly (DESIGN §24): an agent discovers,
writes, loads and revises a skill in a mount through the same dispatcher
and receipts as memory — the version rows carry `conv:<id>#<turn>`."""

from pathlib import Path

import pytest

from neosian import AgentConfig, Model
from neosian._foundation.conversation.core import Conversation
from neosian._foundation.llm.base import Role, ToolCall
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import Mount
from neosian._foundation.shared.types import ToolCallId, ToolName

PROJECT = "user:demo/proj:app"
MOUNTS = (Mount("user:demo", "user"), Mount(PROJECT, "project"))
RELEASE = "---\ndescription: Release this project\n---\n1. bump\n2. tag"


def _call(name: str, arguments: dict[str, object]) -> FakeTurn:
    return FakeTurn(
        tool_calls=(
            ToolCall(id=ToolCallId("c"), name=ToolName(name), arguments=arguments),
        )
    )


def _memory(**arguments: object) -> FakeTurn:
    return _call("memory", {"path": "/project/skills/release", **arguments})


SCRIPT = FakeScript(
    turns=(
        # Turn 1: discover (nothing yet), then write the skill.
        _call("list_skills", {}),
        _memory(command="create", content=RELEASE),
        FakeTurn(content="Saved the release skill."),
        # Turn 2: load it.
        _call("load_skill", {"name": "release"}),
        FakeTurn(content="Bump, then tag."),
        # Turn 3: revise it and read it back.
        _memory(command="str_replace", old_str="2. tag", new_str="2. tag\n3. push"),
        _call("load_skill", {"name": "release"}),
        FakeTurn(content="Bump, tag, push."),
    )
)


@pytest.mark.unit
async def test_an_agent_writes_loads_and_revises_a_skill(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "home")
    fake = FakeClient(SCRIPT)
    config = AgentConfig(
        system_prompt="You are a test agent.",
        model=Model.FAKE,
        enable_todo=False,
        client_factory=lambda _: fake,
    )
    convo = Conversation(config, store=store, conversation_id="t1", mounts=MOUNTS)
    await convo.send("Save a reusable skill for releasing this project.")
    await convo.send("How do I release?")
    await convo.send("Add a push step to the release skill.")

    rows = await store.versions(PROJECT, "skills/release")
    assert [(r.version, r.action, r.actor) for r in rows] == [
        (2, "modified", "conv:t1#3"),
        (1, "created", "conv:t1#1"),
    ]
    tool_texts = [
        str(m.content)
        for call in fake.calls
        for m in call.messages
        if m.role is Role.TOOL
    ]
    # The empty listing carried the writing guide; the last load returned
    # the revised body and named the document's version.
    assert any("To add a skill" in t for t in tool_texts)
    assert any("1. bump" in t and "(version 1)" in t for t in tool_texts)
    assert any("3. push" in t and "(version 2)" in t for t in tool_texts)
    assert [str(t.name) for t in fake.calls[0].tools] == [
        "memory",
        "list_skills",
        "load_skill",
    ]
