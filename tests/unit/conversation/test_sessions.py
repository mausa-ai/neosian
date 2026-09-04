"""The N2 slice-A done-when, pinned keylessly: the three-line quickstart —
construct store, construct conversation, send() — yields a stateful,
memory-bearing agent.

Two "sessions" = two fresh Conversation + FileStore instances over the
same directory and the same conversation_id. Session 1 records a memory
through the tool and gets its turn persisted; session 2 resumes: history
replays verbatim into the model call, the regenerated index lists the
document, and the agent reads it back — durability across process
restarts, exactly what the CLI relaunch exercises.
"""

from pathlib import Path

from neosian import AgentConfig, Model
from neosian._foundation.conversation.core import Conversation
from neosian._foundation.llm.base import Role, ToolCall
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.file import FileStore
from neosian._foundation.shared.types import ToolCallId, ToolName

_SYSTEM = "You are a helpful agent with memory."


def _config(script: FakeScript) -> tuple[AgentConfig, FakeClient]:
    fake = FakeClient(script)
    config = AgentConfig(
        system_prompt=_SYSTEM,
        model=Model.FAKE,
        enable_todo=False,
        client_factory=lambda _: fake,
    )
    return config, fake


def _memory_call(arguments: dict[str, object]) -> FakeTurn:
    return FakeTurn(
        tool_calls=(
            ToolCall(id=ToolCallId("c1"), name=ToolName("memory"), arguments=arguments),
        )
    )


class TestConversationAcrossSessions:
    async def test_resume_with_memory(self, tmp_path: Path) -> None:
        root = tmp_path / "data"

        # Session 1 — the three-line quickstart: the agent records a fact
        # and answers; the turn (tool round included) is persisted.
        config_one, _ = _config(
            FakeScript(
                turns=(
                    _memory_call(
                        {
                            "command": "create",
                            "path": "/memories/preferences",
                            "content": "Prefers espresso.",
                        }
                    ),
                    FakeTurn(content="Noted: espresso."),
                )
            )
        )
        store_one = FileStore(root)
        convo_one = Conversation(
            config_one,
            store=store_one,
            conversation_id="thread-829",
            memory_scope="user:1234",
        )
        response = await convo_one.send("I only drink espresso.")
        assert response.tool_results[0].success
        assert response.message.content == "Noted: espresso."

        # Session 2 — a fresh store and conversation over the same
        # directory and id. Resume replays history; the frozen index now
        # lists the document; the agent views it and answers from memory.
        config_two, fake_two = _config(
            FakeScript(
                turns=(
                    _memory_call({"command": "view", "path": "/memories/preferences"}),
                    FakeTurn(content="You drink espresso."),
                )
            )
        )
        store_two = FileStore(root)
        convo_two = Conversation(
            config_two,
            store=store_two,
            conversation_id="thread-829",
            memory_scope="user:1234",
        )
        response = await convo_two.send("Where did we leave off?")

        # History replayed verbatim into the model call, tool round included.
        replayed = fake_two.calls[0].messages
        assert [m.role for m in replayed[:5]] == [
            Role.SYSTEM,
            Role.USER,
            Role.ASSISTANT,
            Role.TOOL,
            Role.ASSISTANT,
        ]
        assert replayed[1].content == "I only drink espresso."
        # The frozen index (in the system message) lists the document.
        assert "/memories/preferences" in str(replayed[0].content)
        # The tool read the memory back; the answer stands on it.
        assert response.tool_results[0].success
        assert "Prefers espresso." in str(response.tool_results[0].data)
        assert response.message.content == "You drink espresso."

        # The store holds both sessions' turns, numbered continuously.
        turns = await store_two.read_turns("thread-829")
        assert [t.turn for t in turns] == [1, 2]
