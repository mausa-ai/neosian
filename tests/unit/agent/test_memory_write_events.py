"""The `memory_write` frame on the event stream (NP, ledger #99).

One frame per successful mutating memory command, immediately after its
`tool_result`; `view` and failed calls stay silent; the payload is the
receipt, never the content. Fully keyless — FakeClient-scripted tool
calls, real tool execution over a real FileStore.
"""

from pathlib import Path

from neosian import Agent, AgentConfig, Model
from neosian._foundation.agent.events import (
    AgentEvent,
    DoneEvent,
    MemoryWriteEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from neosian._foundation.llm.base import Message, Role, ToolCall
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.shared.types import SystemPrompt, ToolCallId, ToolName

_SYSTEM = SystemPrompt("You are a test agent with memory.")
_USER_MSG = [Message(role=Role.USER, content="remember this")]
_MOUNT = Mount(scope="user:demo", mount_path="user")
_KB = Mount(scope="tenant:acme/kb:main", mount_path="kb", read_only=True)


def _agent(root: Path, script: FakeScript) -> Agent:
    memory = MemoryConfig(store=FileStore(root), mounts=(_MOUNT, _KB))
    fake = FakeClient(script)
    return Agent(
        AgentConfig(
            system_prompt=_SYSTEM,
            model=Model.FAKE,
            enable_todo=False,
            memory=memory,
            client_factory=lambda _: fake,
        )
    )


def _calls(*arguments: dict[str, object]) -> FakeTurn:
    return FakeTurn(
        tool_calls=tuple(
            ToolCall(id=ToolCallId(f"c{i}"), name=ToolName("memory"), arguments=args)
            for i, args in enumerate(arguments, 1)
        )
    )


async def _events(agent: Agent) -> list[AgentEvent]:
    return [event async for event in await agent.run(_USER_MSG, stream=True)]


class TestMemoryWriteFrame:
    async def test_frame_follows_its_tool_result(self, tmp_path: Path) -> None:
        script = FakeScript(
            turns=(
                _calls({"command": "create", "path": "/user/prefs", "content": "tea"}),
                FakeTurn(content="noted"),
            )
        )
        events = await _events(_agent(tmp_path, script))
        (write,) = [e for e in events if isinstance(e, MemoryWriteEvent)]
        (result,) = [e for e in events if isinstance(e, ToolResultEvent)]
        assert write.sequence == result.sequence + 1
        assert write.tool_call_id == result.tool_call_id
        assert write.command == "create"
        assert write.path == "/user/prefs"
        assert write.version == 1
        assert write.previous_path is None
        # The frame precedes the terminal — a host that saw `done` has
        # every receipt of the turn.
        (done,) = [e for e in events if isinstance(e, DoneEvent)]
        assert write.sequence < done.sequence

    async def test_view_emits_nothing(self, tmp_path: Path) -> None:
        script = FakeScript(
            turns=(_calls({"command": "view", "path": "/"}), FakeTurn(content="ok"))
        )
        events = await _events(_agent(tmp_path, script))
        assert [e for e in events if isinstance(e, MemoryWriteEvent)] == []
        assert any(isinstance(e, ToolResultEvent) and e.success for e in events)

    async def test_failed_write_emits_nothing(self, tmp_path: Path) -> None:
        script = FakeScript(
            turns=(
                _calls({"command": "create", "path": "/kb/doc", "content": "x"}),
                FakeTurn(content="ok"),
            )
        )
        events = await _events(_agent(tmp_path, script))
        assert [e for e in events if isinstance(e, MemoryWriteEvent)] == []
        (result,) = [e for e in events if isinstance(e, ToolResultEvent)]
        assert not result.success

    async def test_two_writes_two_frames_each_after_its_result(
        self, tmp_path: Path
    ) -> None:
        script = FakeScript(
            turns=(
                _calls(
                    {"command": "create", "path": "/user/a", "content": "1"},
                    {"command": "create", "path": "/user/b", "content": "2"},
                ),
                FakeTurn(content="ok"),
            )
        )
        events = await _events(_agent(tmp_path, script))
        writes = [e for e in events if isinstance(e, MemoryWriteEvent)]
        assert {w.path for w in writes} == {"/user/a", "/user/b"}
        # Completion order may vary; adjacency to the own result may not —
        # but every frame follows its own call's tool_result.
        by_id = {
            e.tool_call_id: e.sequence for e in events if isinstance(e, ToolResultEvent)
        }
        for write in writes:
            assert write.sequence > by_id[write.tool_call_id]

    async def test_non_memory_tools_never_emit(self, tmp_path: Path) -> None:
        script = FakeScript(
            turns=(
                FakeTurn(
                    tool_calls=(
                        ToolCall(
                            id=ToolCallId("c1"),
                            name=ToolName("no_such_tool"),
                            arguments={},
                        ),
                    )
                ),
                FakeTurn(content="ok"),
            )
        )
        events = await _events(_agent(tmp_path, script))
        assert [e for e in events if isinstance(e, MemoryWriteEvent)] == []

    async def test_sequences_stay_gapless_and_monotonic(self, tmp_path: Path) -> None:
        script = FakeScript(
            turns=(
                _calls({"command": "create", "path": "/user/a", "content": "x"}),
                FakeTurn(content="done"),
            )
        )
        events = await _events(_agent(tmp_path, script))
        assert [e.sequence for e in events] == list(range(1, len(events) + 1))

    async def test_call_result_write_order(self, tmp_path: Path) -> None:
        script = FakeScript(
            turns=(
                _calls({"command": "create", "path": "/user/a", "content": "x"}),
                FakeTurn(content="ok"),
            )
        )
        events = await _events(_agent(tmp_path, script))
        (call,) = [e for e in events if isinstance(e, ToolCallEvent)]
        (result,) = [e for e in events if isinstance(e, ToolResultEvent)]
        (write,) = [e for e in events if isinstance(e, MemoryWriteEvent)]
        assert call.sequence < result.sequence < write.sequence


class TestBlockingPathUnchanged:
    async def test_blocking_run_still_returns(self, tmp_path: Path) -> None:
        """Blocking runs have no event stream by construction — the
        receipt seam must not disturb the response path."""
        script = FakeScript(
            turns=(
                _calls({"command": "create", "path": "/user/a", "content": "x"}),
                FakeTurn(content="stored"),
            )
        )
        response = await _agent(tmp_path, script).run(_USER_MSG, stream=False)
        assert response.message.content == "stored"
