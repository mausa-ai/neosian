"""The N1 done-when, pinned keylessly: memory accumulated in one session
is used in the next.

Two "sessions" = two fresh Agent + FileStore instances over the same
directory. Session 1 writes through the memory tool (FakeClient-scripted
tool calls, real tool execution); session 2 sees the document in a
regenerated index and reads it back. What this proves is durability
across store instances — exactly what the CLI relaunch exercises.
"""

from pathlib import Path

from neosian import Agent, AgentConfig, Model
from neosian._foundation.llm.base import Message, Role, ToolCall
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.index import generate_memory_index
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.shared.types import SystemPrompt, ToolCallId, ToolName

_SYSTEM = SystemPrompt("You are a test agent with memory.")
_MOUNT = Mount(scope="user:demo", mount_path="user", description="user facts")


def _agent(memory: MemoryConfig, script: FakeScript) -> Agent:
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


def _call(arguments: dict[str, object]) -> FakeTurn:
    return FakeTurn(
        tool_calls=(
            ToolCall(id=ToolCallId("c1"), name=ToolName("memory"), arguments=arguments),
        )
    )


class TestMemoryWiring:
    def test_memory_config_registers_the_tool(self, tmp_path: Path) -> None:
        memory = MemoryConfig(store=FileStore(tmp_path), mounts=(_MOUNT,))
        agent = Agent(
            AgentConfig(
                system_prompt=_SYSTEM,
                model=Model.FAKE,
                enable_todo=False,
                memory=memory,
            )
        )
        # The skill tools ride with memory (§24).
        assert [str(name) for name in agent._tools] == [
            "memory",
            "list_skills",
            "load_skill",
        ]

    def test_no_memory_registers_nothing(self) -> None:
        agent = Agent(
            AgentConfig(system_prompt=_SYSTEM, model=Model.FAKE, enable_todo=False)
        )
        assert not agent._tools


class TestCrossSessionMemory:
    async def test_accumulate_then_recall(self, tmp_path: Path) -> None:
        root = tmp_path / "memory"
        user = [Message(role=Role.USER, content="I only drink espresso.")]

        # Session 1: the agent records a fact through the memory tool.
        memory_one = MemoryConfig(store=FileStore(root), mounts=(_MOUNT,))
        agent_one = _agent(
            memory_one,
            FakeScript(
                turns=(
                    _call(
                        {
                            "command": "create",
                            "path": "/user/preferences",
                            "content": "Prefers espresso.",
                        }
                    ),
                    FakeTurn(content="Noted."),
                )
            ),
        )
        response = await agent_one.run(user, stream=False)
        assert response.tool_results[0].success
        assert (await memory_one.store.read(_MOUNT.scope, "preferences")) is not None

        # Session 2: a fresh store + agent over the same directory. The
        # regenerated index (the caller injects it at conversation start)
        # lists the document, and `view` reads it back.
        memory_two = MemoryConfig(store=FileStore(root), mounts=(_MOUNT,))
        index = await generate_memory_index(memory_two.store, memory_two.mounts)
        assert "- /user/preferences" in index

        agent_two = _agent(
            memory_two,
            FakeScript(
                turns=(
                    _call({"command": "view", "path": "/user/preferences"}),
                    FakeTurn(content="You prefer espresso."),
                )
            ),
        )
        response = await agent_two.run(
            [Message(role=Role.USER, content="What do I drink?")], stream=False
        )
        assert response.tool_results[0].success
        assert "Prefers espresso." in str(response.tool_results[0].data)
        assert response.message.content == "You prefer espresso."
