"""Integration tests for Cerebras LLM client.

Requires CEREBRAS_API_KEY environment variable.
Run with: CEREBRAS_API_KEY=xxx uv run pytest -m integration -v
"""

import pytest

from neosian._foundation.llm.base import Message, Role, ToolDefinition
from neosian._foundation.llm.cerebras import CerebrasClient
from neosian._foundation.shared.types import Model, ReasoningEffort, ToolName


@pytest.mark.integration
class TestCerebrasCompletion:
    """Test real Cerebras API completions."""

    @pytest.mark.asyncio
    async def test_simple_completion(self, cerebras_client: CerebrasClient) -> None:
        """Test basic completion without tools."""
        messages = [
            Message(role=Role.USER, content="Say 'hello' and nothing else."),
        ]

        response = await cerebras_client.complete(
            messages=messages,
            model=Model.CEREBRAS_GEMMA_4_31B,
        )

        assert response.message.role == Role.ASSISTANT
        assert response.message.content is not None
        assert len(response.message.content) > 0
        assert response.usage.input_tokens > 0
        assert response.usage.output_tokens > 0

    @pytest.mark.asyncio
    async def test_completion_with_system_prompt(
        self, cerebras_client: CerebrasClient
    ) -> None:
        """Test completion with system message."""
        messages = [
            Message(role=Role.SYSTEM, content="You only respond with 'PONG'."),
            Message(role=Role.USER, content="PING"),
        ]

        response = await cerebras_client.complete(
            messages=messages,
            model=Model.CEREBRAS_GEMMA_4_31B,
        )

        assert response.message.content is not None
        assert "PONG" in response.message.content.upper()

    @pytest.mark.asyncio
    async def test_completion_with_tool_call(
        self, cerebras_client: CerebrasClient
    ) -> None:
        """Test that model can request tool calls."""
        messages = [
            Message(
                role=Role.SYSTEM,
                content="You have access to a calculator. Use it for any math.",
            ),
            Message(role=Role.USER, content="What is 15 + 27?"),
        ]

        tools = [
            ToolDefinition(
                name=ToolName("calculate"),
                description="Perform arithmetic calculations",
                parameters={
                    "type": "object",
                    "properties": {
                        "expression": {
                            "type": "string",
                            "description": "Math expression to evaluate",
                        },
                    },
                    "required": ["expression"],
                },
            ),
        ]

        response = await cerebras_client.complete(
            messages=messages,
            model=Model.CEREBRAS_GPT_OSS_120B,
            tools=tools,
        )

        assert response.message.role == Role.ASSISTANT
        has_response = (
            response.message.content is not None or len(response.message.tool_calls) > 0
        )
        assert has_response


@pytest.mark.integration
class TestCerebrasStreaming:
    """Test real Cerebras API streaming."""

    @pytest.mark.asyncio
    async def test_streaming_content(self, cerebras_client: CerebrasClient) -> None:
        """Test streaming returns content chunks."""
        messages = [
            Message(role=Role.USER, content="Count from 1 to 5."),
        ]

        chunks = []
        async for chunk in cerebras_client.stream(
            messages=messages,
            model=Model.CEREBRAS_GEMMA_4_31B,
        ):
            chunks.append(chunk)

        assert len(chunks) > 0

        content_chunks = [c for c in chunks if c.content]
        assert len(content_chunks) > 0

        finish_chunks = [c for c in chunks if c.finish_reason is not None]
        assert len(finish_chunks) > 0


@pytest.mark.integration
class TestCerebrasReasoningEffort:
    """Test reasoning_effort parameter with real Cerebras API."""

    @pytest.mark.asyncio
    async def test_reasoning_effort_high_completion(
        self, cerebras_client: CerebrasClient
    ) -> None:
        """Test completion with reasoning_effort=HIGH."""
        messages = [
            Message(
                role=Role.USER,
                content="What is 17 * 23? Think step by step.",
            ),
        ]

        response = await cerebras_client.complete(
            messages=messages,
            model=Model.CEREBRAS_GPT_OSS_120B,
            reasoning_effort=ReasoningEffort.HIGH,
        )

        assert response.message.role == Role.ASSISTANT
        assert response.message.content is not None
        assert "391" in response.message.content
        assert response.usage.input_tokens > 0
        assert response.usage.output_tokens > 0

    @pytest.mark.asyncio
    async def test_reasoning_effort_low_completion(
        self, cerebras_client: CerebrasClient
    ) -> None:
        """Test completion with reasoning_effort=LOW."""
        messages = [
            Message(role=Role.USER, content="What is 2 + 2?"),
        ]

        response = await cerebras_client.complete(
            messages=messages,
            model=Model.CEREBRAS_GPT_OSS_120B,
            reasoning_effort=ReasoningEffort.LOW,
        )

        assert response.message.role == Role.ASSISTANT
        assert response.message.content is not None
        assert "4" in response.message.content

    @pytest.mark.asyncio
    async def test_reasoning_effort_streaming(
        self, cerebras_client: CerebrasClient
    ) -> None:
        """Test streaming with reasoning_effort parameter."""
        messages = [
            Message(role=Role.USER, content="What is 5 + 7?"),
        ]

        chunks = []
        async for chunk in cerebras_client.stream(
            messages=messages,
            model=Model.CEREBRAS_GPT_OSS_120B,
            reasoning_effort=ReasoningEffort.MEDIUM,
        ):
            chunks.append(chunk)

        assert len(chunks) > 0

        content = "".join(c.content or "" for c in chunks)
        assert "12" in content

        finish_chunks = [c for c in chunks if c.finish_reason is not None]
        assert len(finish_chunks) > 0


@pytest.mark.integration
class TestCerebrasReasoningContent:
    """Test reasoning content parsing with real Cerebras API."""

    @pytest.mark.asyncio
    async def test_completion_returns_reasoning_content(
        self, cerebras_client: CerebrasClient
    ) -> None:
        """Test that completion with reasoning_effort returns reasoning content."""
        messages = [
            Message(
                role=Role.USER,
                content="What is 17 * 23? Show your work.",
            ),
        ]

        response = await cerebras_client.complete(
            messages=messages,
            model=Model.CEREBRAS_GPT_OSS_120B,
            reasoning_effort=ReasoningEffort.HIGH,
        )

        assert response.message.role == Role.ASSISTANT
        assert response.message.content is not None

    @pytest.mark.asyncio
    async def test_streaming_returns_reasoning_chunks(
        self, cerebras_client: CerebrasClient
    ) -> None:
        """Test that streaming with reasoning_effort returns reasoning chunks."""
        messages = [
            Message(
                role=Role.USER,
                content="What is 8 + 9? Think step by step.",
            ),
        ]

        chunks = []
        async for chunk in cerebras_client.stream(
            messages=messages,
            model=Model.CEREBRAS_GPT_OSS_120B,
            reasoning_effort=ReasoningEffort.HIGH,
        ):
            chunks.append(chunk)

        assert len(chunks) > 0

        reasoning_text = "".join(c.reasoning or "" for c in chunks)
        content_text = "".join(c.content or "" for c in chunks)

        reasoning_chunks = [c for c in chunks if c.reasoning]
        content_chunks = [c for c in chunks if c.content]

        assert len(reasoning_chunks) > 0 or len(content_chunks) > 0
        assert "17" in reasoning_text or "17" in content_text

        finish_chunks = [c for c in chunks if c.finish_reason is not None]
        assert len(finish_chunks) > 0
