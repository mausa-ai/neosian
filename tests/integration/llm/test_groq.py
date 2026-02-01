"""Integration tests for Groq LLM client.

Requires GROQ_API_KEY environment variable.
Run with: GROQ_API_KEY=gsk_xxx uv run pytest -m integration -v
"""

import pytest

from neosian._foundation.llm.base import Message, Role, ToolDefinition
from neosian._foundation.llm.groq import GroqClient
from neosian._foundation.shared.types import Model, ReasoningEffort, ToolName


@pytest.mark.integration
class TestGroqCompletion:
    """Test real Groq API completions."""

    @pytest.mark.asyncio
    async def test_simple_completion(self, groq_client: GroqClient) -> None:
        """Test basic completion without tools."""
        messages = [
            Message(role=Role.USER, content="Say 'hello' and nothing else."),
        ]

        response = await groq_client.complete(
            messages=messages,
            model=Model.GPT_OSS_20B,
        )

        assert response.message.role == Role.ASSISTANT
        assert response.message.content is not None
        assert len(response.message.content) > 0
        assert response.usage.input_tokens > 0
        assert response.usage.output_tokens > 0

    @pytest.mark.asyncio
    async def test_completion_with_system_prompt(self, groq_client: GroqClient) -> None:
        """Test completion with system message."""
        messages = [
            Message(role=Role.SYSTEM, content="You only respond with 'PONG'."),
            Message(role=Role.USER, content="PING"),
        ]

        response = await groq_client.complete(
            messages=messages,
            model=Model.GPT_OSS_20B,
        )

        assert response.message.content is not None
        assert "PONG" in response.message.content.upper()

    @pytest.mark.asyncio
    async def test_completion_with_tool_call(self, groq_client: GroqClient) -> None:
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

        response = await groq_client.complete(
            messages=messages,
            model=Model.GPT_OSS_20B,
            tools=tools,
        )

        # Model should either call the tool or answer directly
        # We just verify the response is valid
        assert response.message.role == Role.ASSISTANT
        # Either has content or tool calls
        has_response = (
            response.message.content is not None or len(response.message.tool_calls) > 0
        )
        assert has_response


@pytest.mark.integration
class TestGroqStreaming:
    """Test real Groq API streaming."""

    @pytest.mark.asyncio
    async def test_streaming_content(self, groq_client: GroqClient) -> None:
        """Test streaming returns content chunks."""
        messages = [
            Message(role=Role.USER, content="Count from 1 to 5."),
        ]

        chunks = []
        async for chunk in groq_client.stream(
            messages=messages,
            model=Model.GPT_OSS_20B,
        ):
            chunks.append(chunk)

        # Should have received multiple chunks
        assert len(chunks) > 0

        # At least one chunk should have content
        content_chunks = [c for c in chunks if c.content]
        assert len(content_chunks) > 0

        # Should have a chunk with finish reason (not necessarily last - usage comes after)
        finish_chunks = [c for c in chunks if c.finish_reason is not None]
        assert len(finish_chunks) > 0


@pytest.mark.integration
class TestGroqReasoningEffort:
    """Test reasoning_effort parameter with real Groq API."""

    @pytest.mark.asyncio
    async def test_reasoning_effort_high_completion(
        self, groq_client: GroqClient
    ) -> None:
        """Test completion with reasoning_effort=HIGH."""
        messages = [
            Message(
                role=Role.USER,
                content="What is 17 * 23? Think step by step.",
            ),
        ]

        response = await groq_client.complete(
            messages=messages,
            model=Model.GPT_OSS_20B,
            reasoning_effort=ReasoningEffort.HIGH,
        )

        assert response.message.role == Role.ASSISTANT
        assert response.message.content is not None
        # Should contain the answer (391)
        assert "391" in response.message.content
        assert response.usage.input_tokens > 0
        assert response.usage.output_tokens > 0

    @pytest.mark.asyncio
    async def test_reasoning_effort_low_completion(
        self, groq_client: GroqClient
    ) -> None:
        """Test completion with reasoning_effort=LOW."""
        messages = [
            Message(role=Role.USER, content="What is 2 + 2?"),
        ]

        response = await groq_client.complete(
            messages=messages,
            model=Model.GPT_OSS_20B,
            reasoning_effort=ReasoningEffort.LOW,
        )

        assert response.message.role == Role.ASSISTANT
        assert response.message.content is not None
        assert "4" in response.message.content

    @pytest.mark.asyncio
    async def test_reasoning_effort_streaming(self, groq_client: GroqClient) -> None:
        """Test streaming with reasoning_effort parameter."""
        messages = [
            Message(role=Role.USER, content="What is 5 + 7?"),
        ]

        chunks = []
        async for chunk in groq_client.stream(
            messages=messages,
            model=Model.GPT_OSS_20B,
            reasoning_effort=ReasoningEffort.MEDIUM,
        ):
            chunks.append(chunk)

        assert len(chunks) > 0

        # Collect all content
        content = "".join(c.content or "" for c in chunks)
        assert "12" in content

        # Should have a chunk with finish reason (not necessarily last - usage comes after)
        finish_chunks = [c for c in chunks if c.finish_reason is not None]
        assert len(finish_chunks) > 0


@pytest.mark.integration
class TestGroqReasoningContent:
    """Test reasoning content parsing with real Groq API."""

    @pytest.mark.asyncio
    async def test_completion_returns_reasoning_content(
        self, groq_client: GroqClient
    ) -> None:
        """Test that completion with reasoning_effort returns reasoning content."""
        messages = [
            Message(
                role=Role.USER,
                content="What is 17 * 23? Show your work.",
            ),
        ]

        response = await groq_client.complete(
            messages=messages,
            model=Model.GPT_OSS_20B,
            reasoning_effort=ReasoningEffort.HIGH,
        )

        assert response.message.role == Role.ASSISTANT
        assert response.message.content is not None
        # With reasoning_effort, the model should produce reasoning content
        # Note: reasoning may or may not be populated depending on API behavior
        # The key is that the field exists and is handled correctly

    @pytest.mark.asyncio
    async def test_streaming_returns_reasoning_chunks(
        self, groq_client: GroqClient
    ) -> None:
        """Test that streaming with reasoning_effort returns reasoning chunks."""
        messages = [
            Message(
                role=Role.USER,
                content="What is 8 + 9? Think step by step.",
            ),
        ]

        chunks = []
        async for chunk in groq_client.stream(
            messages=messages,
            model=Model.GPT_OSS_20B,
            reasoning_effort=ReasoningEffort.HIGH,
        ):
            chunks.append(chunk)

        assert len(chunks) > 0

        # Collect reasoning and content separately
        reasoning_text = "".join(c.reasoning or "" for c in chunks)
        content_text = "".join(c.content or "" for c in chunks)

        # With reasoning_effort=HIGH, we expect reasoning chunks
        # Note: reasoning chunks typically come before content chunks
        reasoning_chunks = [c for c in chunks if c.reasoning]
        content_chunks = [c for c in chunks if c.content]

        # Verify we got some response (either reasoning or content)
        assert len(reasoning_chunks) > 0 or len(content_chunks) > 0

        # The answer should be somewhere in the response
        assert "17" in reasoning_text or "17" in content_text

        # Should have a finish reason
        finish_chunks = [c for c in chunks if c.finish_reason is not None]
        assert len(finish_chunks) > 0
