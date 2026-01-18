"""Integration tests for Groq LLM client.

Requires GROQ_API_KEY environment variable.
Run with: GROQ_API_KEY=gsk_xxx uv run pytest -m integration -v
"""

import pytest

from neosian._foundation.llm.base import Message, Role, ToolDefinition
from neosian._foundation.llm.groq import GroqClient
from neosian._foundation.shared.types import Model, ToolName


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

        # Last chunk should have finish reason
        assert chunks[-1].finish_reason is not None
