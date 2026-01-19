"""Integration tests for AgentSession with real LLM.

Requires GROQ_API_KEY environment variable.
Run with: GROQ_API_KEY=gsk_xxx uv run pytest -m integration -v
"""

import pytest

from neosian._foundation.agent.base import Agent
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.shared.types import AgentConfig, Model, SystemPrompt
from neosian._foundation.tools.base import Tool, ToolResult


@pytest.mark.integration
class TestAgentSessionWithGroq:
    """Test AgentSession with real Groq API."""

    @pytest.mark.asyncio
    async def test_session_simple_conversation(self) -> None:
        """Test session handles simple conversation."""
        config = AgentConfig(
            system_prompt=SystemPrompt("You are a helpful assistant. Be concise."),
            tools=[],
            model=Model.GPT_OSS_20B,
            enable_todo=False,
        )
        agent = Agent(config=config)

        async with agent.session() as session:
            messages = [
                Message(role=Role.USER, content="What is 2 + 2? Just the number.")
            ]
            response = await session.run(messages, stream=False)

            assert response.message.role == Role.ASSISTANT
            assert response.message.content is not None
            assert "4" in response.message.content

    @pytest.mark.asyncio
    async def test_session_multiple_runs_reuse_client(self) -> None:
        """Test multiple runs within a session reuse the same client."""
        config = AgentConfig(
            system_prompt=SystemPrompt("You are a helpful assistant. Be concise."),
            tools=[],
            model=Model.GPT_OSS_20B,
            enable_todo=False,
        )
        agent = Agent(config=config)

        async with agent.session() as session:
            # First run
            messages1 = [Message(role=Role.USER, content="Say 'hello'")]
            response1 = await session.run(messages1, stream=False)

            # Check client was cached
            assert len(session._clients) == 1

            # Second run - should reuse cached client
            messages2 = [Message(role=Role.USER, content="Say 'world'")]
            response2 = await session.run(messages2, stream=False)

            # Still only one cached client
            assert len(session._clients) == 1

            # Both responses should be valid
            assert response1.message.content is not None
            assert response2.message.content is not None

    @pytest.mark.asyncio
    async def test_session_with_tool_execution(self) -> None:
        """Test session executes tools correctly."""

        @Tool(name="get_number", description="Get a specific number")
        async def get_number() -> ToolResult[int]:
            return ToolResult.ok(42)

        config = AgentConfig(
            system_prompt=SystemPrompt(
                "You are a number assistant. Use the get_number tool when asked."
            ),
            tools=[get_number],
            model=Model.GPT_OSS_20B,
            enable_todo=False,
        )
        agent = Agent(config=config)

        async with agent.session() as session:
            messages = [Message(role=Role.USER, content="What number do you have?")]
            response = await session.run(messages, stream=False)

            assert response.message.role == Role.ASSISTANT
            assert response.message.content is not None

            # Should have called the tool
            assert len(response.tool_calls_made) >= 1
            assert any(tc.name == "get_number" for tc in response.tool_calls_made)

    @pytest.mark.asyncio
    async def test_session_streaming(self) -> None:
        """Test session handles streaming correctly."""
        config = AgentConfig(
            system_prompt=SystemPrompt("You are a helpful assistant. Be concise."),
            tools=[],
            model=Model.GPT_OSS_20B,
            enable_todo=False,
        )
        agent = Agent(config=config)

        async with agent.session() as session:
            messages = [Message(role=Role.USER, content="Say 'hi' in one word.")]
            result = await session.run(messages, stream=True)

            events = []
            async for sse in result:
                events.append(sse)

            # Should have at least content and done events
            assert len(events) >= 2
            assert any("content" in e for e in events)
            assert any("done" in e for e in events)

    @pytest.mark.asyncio
    async def test_session_context_manager_cleanup(self) -> None:
        """Test session properly cleans up on exit."""
        config = AgentConfig(
            system_prompt=SystemPrompt("You are a helpful assistant."),
            tools=[],
            model=Model.GPT_OSS_20B,
            enable_todo=False,
        )
        agent = Agent(config=config)

        async with agent.session() as session:
            messages = [Message(role=Role.USER, content="Hi")]
            await session.run(messages, stream=False)

            # Client should be cached during session
            assert len(session._clients) == 1

        # After session exit, clients should be cleared
        assert len(session._clients) == 0

    @pytest.mark.asyncio
    async def test_session_conversation_flow(self) -> None:
        """Test multi-turn conversation within a session."""
        config = AgentConfig(
            system_prompt=SystemPrompt(
                "You are a helpful assistant. Keep responses very short."
            ),
            tools=[],
            model=Model.GPT_OSS_20B,
            enable_todo=False,
        )
        agent = Agent(config=config)

        async with agent.session() as session:
            # Turn 1
            messages = [Message(role=Role.USER, content="My name is Alice.")]
            response1 = await session.run(messages, stream=False)
            messages.append(response1.message)

            # Turn 2 - continue conversation
            messages.append(Message(role=Role.USER, content="What's my name?"))
            response2 = await session.run(messages, stream=False)

            # Model should remember the name from context
            assert response2.message.content is not None
            # The name should appear in the response
            assert "alice" in response2.message.content.lower()

            # Only one client used throughout
            assert len(session._clients) == 1
