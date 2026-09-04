"""Governance: memory_write frames, undo, and edit-only mounts (NP).

Every successful mutating memory command puts one content-free
`memory_write` frame on the event stream right after its `tool_result`;
its `{path, version}` is exactly what `revert_memory` takes, so a host
renders "remembered X · undo" from the wire alone. Redaction is the
operator's act (`neosian memory redact`, in the tour's shell transcript).
An edit-only mount fixes the document set: a model that tries to create
a new file gets a corrective failure and adapts, while edits to existing
documents still land.

Usage:
    ANTHROPIC_API_KEY=... python examples/governance_example.py
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from neosian import (
    AgentConfig,
    Conversation,
    FileStore,
    MemoryConfig,
    MemoryWriteEvent,
    Model,
    Mount,
    ReflectionConfig,
    create_memory_tool,
    revert_memory,
)

_ROOT = Path(__file__).resolve().parent.parent / ".neosian" / "tour"
_SCOPE = "user:tour"
_STORE = FileStore(_ROOT)
# The operator's handle on the mount `memory_scope=` builds for the agent.
_MEMORY = MemoryConfig(
    store=_STORE, mounts=(Mount(scope=_SCOPE, mount_path="memories"),)
)
_SHOWN = {"tool_call", "tool_result", "memory_write", "done"}

configuration = AgentConfig(
    system_prompt="You are a concise assistant. Answer in one sentence.",
    model=Model.CLAUDE_SONNET_5,
    enable_todo=False,
)


async def main() -> None:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")

    print("--- the write, frame by frame")
    writes: list[MemoryWriteEvent] = []
    convo = Conversation(
        configuration,
        store=_STORE,
        conversation_id=f"{stamp}-write",
        memory_scope=_SCOPE,
        reflection=ReflectionConfig(enabled=False),
    )
    async with convo:
        events = await convo.send(
            "Remember that our on-call rotation hands over on Mondays at 09:00 UTC.",
            stream=True,
        )
        async for event in events:
            if event.type.value in _SHOWN:
                print(f"  {str(event.to_dict())[:160]}")
            if isinstance(event, MemoryWriteEvent):
                writes.append(event)

    print("--- undo, from the frame alone")
    newest = writes[-1]
    undone = await revert_memory(
        _MEMORY, newest.path, version=newest.version, actor="tour:undo"
    )
    print(f"  success={undone.success} receipt={undone.receipt}")

    print("--- an edit-only mount: the document set is fixed")
    seeded = await create_memory_tool(_MEMORY)(
        command="create",
        path="/memories/team.md",
        content="Team: platform. Deploy day: Thursday.\n",
    )
    print(f"  seeded /memories/team.md: success={seeded.success}")
    fixed = Conversation(
        configuration,
        store=_STORE,
        conversation_id=f"{stamp}-edit-only",
        mounts=[Mount(scope=_SCOPE, mount_path="memories", edit_only=True)],
        reflection=ReflectionConfig(enabled=False),
    )
    async with fixed:
        for prompt in (
            "Create a new memory file /memories/vendors.md listing our vendor, Acme Corp.",
            "Update the team note: deploy day moved to Wednesday.",
        ):
            print(f"user: {prompt}")
            response = await fixed.send(prompt)
            for result in response.tool_results:
                verdict = "ok" if result.success else "refused"
                print(f"  [memory] {verdict}: {str(result.error or result.data)[:120]}")
            print(f"agent: {response.message.content}")


if __name__ == "__main__":
    asyncio.run(main())
