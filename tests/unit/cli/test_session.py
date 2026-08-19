"""Unit tests for session management."""

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from neosian._cli.session import Session
from neosian._foundation.llm.base import Message, Role


@pytest.mark.unit
class TestSession:
    """Tests for Session class."""

    def test_empty_session(self) -> None:
        """Test creating an empty session."""
        session = Session()

        assert session.messages == []
        assert session.agent_name == ""
        assert session.started_at != ""

    def test_session_with_agent_name(self) -> None:
        """Test creating a session with an agent name."""
        session = Session(agent_name="test_agent")

        assert session.agent_name == "test_agent"

    def test_add_message(self) -> None:
        """Test adding a message to session."""
        session = Session()
        message = Message(role=Role.USER, content="Hello")

        session.add_message(message)

        assert len(session.messages) == 1
        assert session.messages[0].content == "Hello"

    def test_add_user_message(self) -> None:
        """Test adding a user message."""
        session = Session()

        session.add_user_message("Hello")

        assert len(session.messages) == 1
        assert session.messages[0].role == Role.USER
        assert session.messages[0].content == "Hello"

    def test_add_assistant_message(self) -> None:
        """Test adding an assistant message."""
        session = Session()

        session.add_assistant_message("Hi there!")

        assert len(session.messages) == 1
        assert session.messages[0].role == Role.ASSISTANT
        assert session.messages[0].content == "Hi there!"

    def test_get_messages_excludes_system(self) -> None:
        """Test get_messages excludes system messages."""
        session = Session()
        session.add_message(Message(role=Role.SYSTEM, content="System"))
        session.add_user_message("User")
        session.add_assistant_message("Assistant")

        messages = session.get_messages()

        assert len(messages) == 2
        assert all(m.role != Role.SYSTEM for m in messages)

    def test_to_dict(self) -> None:
        """Test converting session to dictionary."""
        session = Session(agent_name="test_agent")
        session.add_user_message("Hello")
        session.add_assistant_message("Hi!")

        data: dict[str, Any] = session.to_dict()

        assert data["agent_name"] == "test_agent"
        assert "started_at" in data
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][0]["content"] == "Hello"
        assert data["messages"][1]["role"] == "assistant"
        assert data["messages"][1]["content"] == "Hi!"

    def test_save_creates_file(self) -> None:
        """Test saving session creates a JSON file."""
        session = Session(agent_name="test_agent")
        session.add_user_message("Hello")

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "session.json"
            result = session.save(path)

            assert result == path
            assert path.exists()

            with path.open() as f:
                data = json.load(f)

            assert data["agent_name"] == "test_agent"
            assert len(data["messages"]) == 1

    def test_save_creates_parent_directories(self) -> None:
        """Test save creates parent directories if needed."""
        session = Session(agent_name="test")
        session.add_user_message("Hello")

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "nested" / "dir" / "session.json"
            result = session.save(path)

            assert result == path
            assert path.exists()

    def test_generate_filename(self) -> None:
        """Test filename generation."""
        session = Session(agent_name="my_agent")

        filename = session.generate_filename()

        assert "my_agent" in filename
        assert filename.endswith(".json")
        # Should have date format: YYYY-MM-DD_HH-MM_name.json
        assert "_" in filename


@pytest.mark.unit
class TestSessionPersistReplay:
    """The N0 done-when: a full multi-turn session — intermediate tool
    messages included — persists and replays faithfully, keylessly."""

    async def test_tool_session_save_load_replay(self, tmp_path: Path) -> None:
        from neosian import Agent, AgentConfig, Model, Tool, ToolResult
        from neosian._foundation.llm.base import ToolCall
        from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
        from neosian._foundation.shared.types import (
            SystemPrompt,
            ToolCallId,
            ToolName,
        )

        @Tool(name="add", description="Add two numbers")
        async def add(a: int, b: int) -> ToolResult[int]:
            return ToolResult.ok(a + b)

        script = FakeScript(
            turns=(
                FakeTurn(
                    tool_calls=(
                        ToolCall(
                            id=ToolCallId("call_1"),
                            name=ToolName("add"),
                            arguments={"a": 2, "b": 3},
                        ),
                    )
                ),
                FakeTurn(content="The sum is 5.", reasoning="2+3"),
                FakeTurn(content="You asked what 2 + 3 is."),
            )
        )
        fake = FakeClient(script)
        agent = Agent(
            AgentConfig(
                system_prompt=SystemPrompt("calc"),
                tools=[add],
                enable_todo=False,
                model=Model.FAKE,
                client_factory=lambda _: fake,
            )
        )

        # Two turns, persisted exactly as the playground chat loop does:
        # user message + turn_messages verbatim.
        session = Session(agent_name="calc")
        for user_input in ["What is 2 + 3?", "What did I ask?"]:
            messages = [
                *session.get_messages(),
                Message(role=Role.USER, content=user_input),
            ]
            response = await agent.run(messages, stream=False)
            session.add_user_message(user_input)
            for message in response.turn_messages:
                session.add_message(message)

        # The intermediate tool round is in the session
        roles = [m.role for m in session.messages]
        assert Role.TOOL in roles
        tool_round = next(m for m in session.messages if m.tool_calls)
        assert tool_round.tool_calls[0].name == "add"

        # Save → load: byte-faithful messages
        path = session.save(tmp_path / "session.json")
        loaded = Session.load(path)
        assert loaded.messages == session.messages
        assert loaded.agent_name == "calc"

        # The loaded history replays as valid input for the next turn
        replay = [*loaded.get_messages(), Message(role=Role.USER, content="again?")]
        again = FakeClient(FakeScript(turns=(FakeTurn(content="ok"),)))
        agent2 = Agent(
            AgentConfig(
                system_prompt=SystemPrompt("calc"),
                tools=[add],
                enable_todo=False,
                model=Model.FAKE,
                client_factory=lambda _: again,
            )
        )
        await agent2.run(replay, stream=False)
        sent = again.calls[0].messages
        assert [m.role for m in sent].count(Role.TOOL) == 1  # tool history intact


@pytest.mark.unit
class TestSessionMultimodalPersistence:
    """Sessions with block-list content must stay JSON-serializable."""

    def test_to_dict_with_block_content_is_json_serializable(self) -> None:
        from neosian._foundation.llm.base import DocumentBlock, TextBlock

        session = Session(agent_name="test-agent")
        session.add_message(
            Message(
                role=Role.USER,
                content=[
                    DocumentBlock(media_type="application/pdf", data="JVBERi0="),
                    TextBlock(text="Transcribe this."),
                ],
            )
        )
        session.add_assistant_message("# Transcription")

        data: dict[str, Any] = session.to_dict()
        encoded = json.dumps(data)  # Must not raise

        assert "Transcribe this." in encoded
        messages = data["messages"]
        assert isinstance(messages, list)
        assert messages[0]["content"][0]["type"] == "document"
