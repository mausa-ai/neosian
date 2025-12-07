"""Unit tests for session management."""

import json
import tempfile
from pathlib import Path

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

        data = session.to_dict()

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
