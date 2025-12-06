"""Integration tests for Agent with real LLM.

Requires GROQ_API_KEY environment variable.
Run with: GROQ_API_KEY=gsk_xxx uv run pytest -m integration -v
"""

import pytest

from neosian._foundation.agent.base import Agent
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.llm.groq import GroqClient
from neosian._foundation.shared.constants import Provider
from neosian._foundation.shared.types import ModelId, SystemPrompt
from neosian._foundation.tools.base import Tool, ToolResult


@pytest.mark.integration
class TestAgentWithGroq:
    """Test Agent with real Groq API."""

    @pytest.mark.asyncio
    async def test_simple_conversation(self, groq_client: GroqClient) -> None:
        """Test agent handles simple conversation without tools."""
        agent = Agent(
            client=groq_client,
            model=ModelId(Provider.Groq.DEFAULT_MODEL),
            system_prompt=SystemPrompt("You are a helpful assistant. Be concise."),
        )

        messages = [Message(role=Role.USER, content="What is 2 + 2? Just the number.")]
        response = await agent.run(messages)

        assert response.message.role == Role.ASSISTANT
        assert response.message.content is not None
        assert "4" in response.message.content
        assert len(response.tool_calls_made) == 0

    @pytest.mark.asyncio
    async def test_agent_with_tool_execution(self, groq_client: GroqClient) -> None:
        """Test agent executes tools and returns result."""

        @Tool(name="get_weather", description="Get current weather for a city")
        async def get_weather(city: str) -> ToolResult[str]:
            # Fake weather data
            return ToolResult.ok(f"The weather in {city} is sunny, 22°C")

        agent = Agent(
            client=groq_client,
            model=ModelId(Provider.Groq.DEFAULT_MODEL),
            system_prompt=SystemPrompt(
                "You are a weather assistant. Use the get_weather tool to answer questions."
            ),
            tools=[get_weather],
        )

        messages = [Message(role=Role.USER, content="What's the weather in Tokyo?")]
        response = await agent.run(messages)

        assert response.message.role == Role.ASSISTANT
        assert response.message.content is not None

        # Should have called the weather tool
        assert len(response.tool_calls_made) >= 1
        assert any(tc.name == "get_weather" for tc in response.tool_calls_made)

        # Response should mention Tokyo or the weather
        content_lower = response.message.content.lower()
        assert (
            "tokyo" in content_lower
            or "sunny" in content_lower
            or "22" in content_lower
        )

    @pytest.mark.asyncio
    async def test_agent_with_multiple_tools(self, groq_client: GroqClient) -> None:
        """Test agent can use multiple tools."""

        @Tool(name="add", description="Add two numbers")
        async def add(a: int, b: int) -> ToolResult[int]:
            return ToolResult.ok(a + b)

        @Tool(name="multiply", description="Multiply two numbers")
        async def multiply(a: int, b: int) -> ToolResult[int]:
            return ToolResult.ok(a * b)

        agent = Agent(
            client=groq_client,
            model=ModelId(Provider.Groq.DEFAULT_MODEL),
            system_prompt=SystemPrompt(
                "You are a calculator. Use the tools for all math operations."
            ),
            tools=[add, multiply],
        )

        messages = [Message(role=Role.USER, content="What is 5 + 3?")]
        response = await agent.run(messages)

        assert response.message.role == Role.ASSISTANT
        assert response.message.content is not None

        # Should have used add tool
        assert len(response.tool_calls_made) >= 1

        # Tool result should be 8
        add_results = [r for r in response.tool_results if r.data == 8]
        assert len(add_results) >= 1

    @pytest.mark.asyncio
    async def test_agent_handles_tool_error(self, groq_client: GroqClient) -> None:
        """Test agent gracefully handles tool errors."""

        @Tool(name="failing_tool", description="A tool that always fails")
        async def failing_tool(input: str) -> ToolResult[str]:  # noqa: ARG001
            return ToolResult.fail("This tool is broken")

        agent = Agent(
            client=groq_client,
            model=ModelId(Provider.Groq.DEFAULT_MODEL),
            system_prompt=SystemPrompt("You have access to a tool. Try to use it."),
            tools=[failing_tool],
        )

        messages = [Message(role=Role.USER, content="Please use the failing_tool.")]
        response = await agent.run(messages)

        # Agent should still respond, handling the error gracefully
        assert response.message.role == Role.ASSISTANT
        assert response.message.content is not None

        # Tool should have been called and failed
        if len(response.tool_results) > 0:
            assert any(not r.success for r in response.tool_results)
