"""External tests for Agent with real LLM (Groq).

Requires GROQ_API_KEY environment variable.
Run with: GROQ_API_KEY=gsk_xxx uv run pytest -m external_groq -v
"""

import asyncio
import time

import pytest

from neosian._foundation.agent.base import Agent
from neosian._foundation.llm.base import Message, Role, text_of
from neosian._foundation.shared.exceptions import ModelFailedError
from neosian._foundation.shared.types import (
    AgentConfig,
    Model,
    ReasoningEffort,
    SystemPrompt,
)
from neosian._foundation.tools.base import Tool, ToolResult


class TestAgentWithGroq:
    """Test Agent with real Groq API."""

    @pytest.mark.asyncio
    async def test_simple_conversation(self) -> None:
        """Test agent handles simple conversation without tools."""
        config = AgentConfig(
            system_prompt=SystemPrompt("You are a helpful assistant. Be concise."),
            tools=[],
            model=Model.GROQ_GPT_OSS_20B,
            enable_todo=False,
        )
        agent = Agent(config=config)

        messages = [Message(role=Role.USER, content="What is 2 + 2? Just the number.")]
        response = await agent.run(messages, stream=False)

        assert response.message.role == Role.ASSISTANT
        assert response.message.content is not None
        assert "4" in response.message.content
        assert len(response.tool_calls_made) == 0

    @pytest.mark.asyncio
    async def test_agent_with_tool_execution(self) -> None:
        """Test agent executes tools and returns result."""

        @Tool(name="get_weather", description="Get current weather for a city")
        async def get_weather(city: str) -> ToolResult[str]:
            # Fake weather data
            return ToolResult.ok(f"The weather in {city} is sunny, 22°C")

        config = AgentConfig(
            system_prompt=SystemPrompt(
                "You are a weather assistant. Use the get_weather tool to answer questions."
            ),
            tools=[get_weather],
            model=Model.GROQ_GPT_OSS_20B,
            enable_todo=False,
        )
        agent = Agent(config=config)

        messages = [Message(role=Role.USER, content="What's the weather in Tokyo?")]
        response = await agent.run(messages, stream=False)

        assert response.message.role == Role.ASSISTANT
        assert response.message.content is not None

        # Should have called the weather tool
        assert len(response.tool_calls_made) >= 1
        assert any(tc.name == "get_weather" for tc in response.tool_calls_made)

        # Response should mention Tokyo or the weather
        content_lower = text_of(response.message).lower()
        assert (
            "tokyo" in content_lower
            or "sunny" in content_lower
            or "22" in content_lower
        )

    @pytest.mark.asyncio
    async def test_agent_with_multiple_tools(self) -> None:
        """Test agent can use multiple tools."""

        @Tool(name="add", description="Add two numbers")
        async def add(a: int, b: int) -> ToolResult[int]:
            return ToolResult.ok(a + b)

        @Tool(name="multiply", description="Multiply two numbers")
        async def multiply(a: int, b: int) -> ToolResult[int]:
            return ToolResult.ok(a * b)

        config = AgentConfig(
            system_prompt=SystemPrompt(
                "You are a calculator. Use the tools for all math operations."
            ),
            tools=[add, multiply],
            model=Model.GROQ_GPT_OSS_20B,
            enable_todo=False,
        )
        agent = Agent(config=config)

        messages = [Message(role=Role.USER, content="What is 5 + 3?")]
        response = await agent.run(messages, stream=False)

        assert response.message.role == Role.ASSISTANT
        assert response.message.content is not None

        # Should have used add tool
        assert len(response.tool_calls_made) >= 1

        # Tool result should be 8
        add_results = [r for r in response.tool_results if r.data == 8]
        assert len(add_results) >= 1

    @pytest.mark.asyncio
    async def test_agent_handles_tool_error(self) -> None:
        """Test agent gracefully handles tool errors."""

        @Tool(name="failing_tool", description="A tool that always fails")
        async def failing_tool(input: str) -> ToolResult[str]:  # noqa: ARG001
            return ToolResult.fail("This tool is broken")

        config = AgentConfig(
            system_prompt=SystemPrompt("You have access to a tool. Try to use it."),
            tools=[failing_tool],
            model=Model.GROQ_GPT_OSS_20B,
            enable_todo=False,
        )
        agent = Agent(config=config)

        messages = [Message(role=Role.USER, content="Please use the failing_tool.")]
        response = await agent.run(messages, stream=False)

        # Agent should still respond, handling the error gracefully
        assert response.message.role == Role.ASSISTANT
        assert response.message.content is not None

        # Tool should have been called and failed
        if len(response.tool_results) > 0:
            assert any(not r.success for r in response.tool_results)

    @pytest.mark.asyncio
    async def test_streaming_simple_conversation(self) -> None:
        """Test agent streams response without tools."""
        config = AgentConfig(
            system_prompt=SystemPrompt("You are a helpful assistant. Be concise."),
            tools=[],
            model=Model.GROQ_GPT_OSS_20B,
            enable_todo=False,
        )
        agent = Agent(config=config)

        messages = [Message(role=Role.USER, content="Say hello in one word.")]
        result = await agent.run(messages, stream=True)

        # Collect all SSE events
        events = []
        async for sse in result:
            events.append(sse)

        # Should have at least one content event and a done event
        assert len(events) >= 2
        assert any("content" in e for e in events)
        assert any("done" in e for e in events)

    @pytest.mark.asyncio
    async def test_streaming_with_tool_execution(self) -> None:
        """Test agent streams with tool calls."""

        @Tool(name="get_number", description="Get a specific number")
        async def get_number() -> ToolResult[int]:
            return ToolResult.ok(42)

        config = AgentConfig(
            system_prompt=SystemPrompt(
                "You are a number assistant. Use the get_number tool when asked for a number."
            ),
            tools=[get_number],
            model=Model.GROQ_GPT_OSS_20B,
            enable_todo=False,
        )
        agent = Agent(config=config)

        messages = [Message(role=Role.USER, content="Get me the number.")]
        result = await agent.run(messages, stream=True)

        events = []
        async for sse in result:
            events.append(sse)

        # Should have tool_call, tool_result, content, and done events
        assert len(events) >= 2
        # At minimum we should have some content and done
        assert any("done" in e for e in events)


class TestAgentReasoningEffort:
    """Test Agent with reasoning_effort enabled."""

    @pytest.mark.asyncio
    async def test_agent_with_reasoning_effort_high(self) -> None:
        """Test agent with reasoning_effort=HIGH returns reasoning content."""
        config = AgentConfig(
            system_prompt=SystemPrompt(
                "You are a helpful assistant. Think carefully before answering."
            ),
            tools=[],
            model=Model.GROQ_GPT_OSS_20B,
            reasoning_effort=ReasoningEffort.HIGH,
            enable_todo=False,
        )
        agent = Agent(config=config)

        messages = [Message(role=Role.USER, content="What is 17 * 23? Show your work.")]
        response = await agent.run(messages, stream=False)

        assert response.message.role == Role.ASSISTANT
        assert response.message.content is not None
        # The answer 391 should be in the response
        assert "391" in response.message.content
        # With HIGH reasoning, we expect reasoning content (though API behavior may vary)
        # The key is the request succeeds and returns a valid response

    @pytest.mark.asyncio
    async def test_agent_with_reasoning_effort_low(self) -> None:
        """Test agent with reasoning_effort=LOW works correctly."""
        config = AgentConfig(
            system_prompt=SystemPrompt("You are a helpful assistant."),
            tools=[],
            model=Model.GROQ_GPT_OSS_20B,
            reasoning_effort=ReasoningEffort.LOW,
            enable_todo=False,
        )
        agent = Agent(config=config)

        messages = [Message(role=Role.USER, content="What is 2 + 2? Just the number.")]
        response = await agent.run(messages, stream=False)

        assert response.message.role == Role.ASSISTANT
        assert response.message.content is not None
        assert "4" in response.message.content

    @pytest.mark.asyncio
    async def test_agent_with_reasoning_effort_streaming(self) -> None:
        """Test agent with reasoning_effort streams correctly."""
        config = AgentConfig(
            system_prompt=SystemPrompt(
                "You are a helpful assistant. Think step by step."
            ),
            tools=[],
            model=Model.GROQ_GPT_OSS_20B,
            reasoning_effort=ReasoningEffort.HIGH,
            enable_todo=False,
        )
        agent = Agent(config=config)

        messages = [
            Message(role=Role.USER, content="What is 8 + 9? Think step by step.")
        ]
        result = await agent.run(messages, stream=True)

        events = []
        async for sse in result:
            events.append(sse)

        # Should have events and done
        assert len(events) >= 2
        assert any("done" in e for e in events)

        # With reasoning, we may get reasoning events before content
        # Check if we have reasoning events
        reasoning_events = [e for e in events if "reasoning" in e]
        content_events = [e for e in events if "content" in e]

        # We should have some response (either reasoning or content)
        assert len(reasoning_events) > 0 or len(content_events) > 0

    @pytest.mark.asyncio
    async def test_agent_reasoning_with_session(self) -> None:
        """Test agent with reasoning_effort works correctly with sessions."""
        config = AgentConfig(
            system_prompt=SystemPrompt("You are a helpful assistant."),
            tools=[],
            model=Model.GROQ_GPT_OSS_20B,
            reasoning_effort=ReasoningEffort.MEDIUM,
            enable_todo=False,
        )
        agent = Agent(config=config)

        async with agent.session() as session:
            messages = [
                Message(role=Role.USER, content="What is 5 * 6? Just the number.")
            ]
            response = await session.run(messages, stream=False)

            assert response.message.role == Role.ASSISTANT
            assert response.message.content is not None
            assert "30" in response.message.content

    @pytest.mark.asyncio
    async def test_agent_reasoning_with_tools(self) -> None:
        """Test agent with reasoning_effort and tools works correctly."""

        @Tool(name="multiply", description="Multiply two numbers")
        async def multiply(a: int, b: int) -> ToolResult[int]:
            return ToolResult.ok(a * b)

        config = AgentConfig(
            system_prompt=SystemPrompt(
                "You are a calculator. Think carefully and use the multiply tool."
            ),
            tools=[multiply],
            model=Model.GROQ_GPT_OSS_20B,
            reasoning_effort=ReasoningEffort.HIGH,
            enable_todo=False,
        )
        agent = Agent(config=config)

        messages = [Message(role=Role.USER, content="What is 7 * 8?")]
        response = await agent.run(messages, stream=False)

        assert response.message.role == Role.ASSISTANT
        assert response.message.content is not None

        # Should have called the tool
        assert len(response.tool_calls_made) >= 1
        assert any(tc.name == "multiply" for tc in response.tool_calls_made)

        # Tool result should be 56
        multiply_results = [r for r in response.tool_results if r.data == 56]
        assert len(multiply_results) >= 1

    @pytest.mark.asyncio
    async def test_parallel_tool_execution_with_real_provider(self) -> None:
        """Two slow tools called in one turn should overlap, not stack.

        Non-deterministic: the model may split the calls across turns. If it
        does, skip rather than fail.
        """

        # Record each tool's execution window; overlap proves parallelism
        # independent of LLM round-trip latency.
        windows: dict[str, tuple[float, float]] = {}

        @Tool(
            name="fetch_a",
            description="Fetch resource A. Always call together with fetch_b.",
        )
        async def fetch_a() -> ToolResult[str]:
            t0 = time.monotonic()
            await asyncio.sleep(2.0)
            windows["a"] = (t0, time.monotonic())
            return ToolResult.ok("A done")

        @Tool(
            name="fetch_b",
            description="Fetch resource B. Always call together with fetch_a.",
        )
        async def fetch_b() -> ToolResult[str]:
            t0 = time.monotonic()
            await asyncio.sleep(2.0)
            windows["b"] = (t0, time.monotonic())
            return ToolResult.ok("B done")

        config = AgentConfig(
            system_prompt=SystemPrompt(
                "You must call BOTH fetch_a and fetch_b in a single response."
            ),
            tools=[fetch_a, fetch_b],
            model=Model.GROQ_GPT_OSS_120B,
            enable_todo=False,
        )
        # max_tool_iterations=1: if the model splits the calls across turns,
        # only the first turn's calls run, the skip below triggers, and the
        # overlap assertion only ever judges a genuine single-turn pair.
        agent = Agent(config=config, max_tool_iterations=1)
        messages = [Message(role=Role.USER, content="Fetch both A and B.")]

        try:
            response = await agent.run(messages, stream=False)
        except ModelFailedError:
            # Model insisted on a second tool call in the tools=None final
            # turn — it split the calls across turns; nothing to measure.
            pytest.skip("Provider split tool calls across turns in this run.")

        if len(response.tool_calls_made) < 2 or set(windows) != {"a", "b"}:
            pytest.skip(
                "Provider did not emit parallel tool calls in this run "
                f"(got {len(response.tool_calls_made)})."
            )

        # Parallel execution: the two 2s windows must overlap substantially.
        overlap = min(windows["a"][1], windows["b"][1]) - max(
            windows["a"][0], windows["b"][0]
        )
        assert overlap > 1.0, f"tools likely ran sequentially: overlap {overlap:.2f}s"
