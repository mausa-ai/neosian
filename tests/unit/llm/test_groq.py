"""Tests for Groq LLM client."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from groq import BadRequestError

from neosian._foundation.llm.base import Message, Role, ToolDefinition
from neosian._foundation.llm.groq import GroqClient
from neosian._foundation.shared.constants import LLMDefaults
from neosian._foundation.shared.exceptions import (
    ProviderError,
    ToolCallGenerationError,
    UnsupportedParameterError,
)
from neosian._foundation.shared.types import Model, ReasoningEffort, ToolName


def _sdk(client: GroqClient) -> Any:
    """The underlying SDK client, untyped for mock wiring and inspection."""
    return client._client


@pytest.mark.unit
class TestGroqClientInit:
    """Test GroqClient initialization."""

    def test_requires_api_key(self) -> None:
        """GroqClient should require an explicit API key."""
        # This should work - explicit key
        client = GroqClient(api_key="test-key")
        assert client is not None

    def test_api_key_is_required_parameter(self) -> None:
        """GroqClient should not accept None as api_key."""
        # The type signature enforces str, not str | None
        # This test documents the expected behavior
        with pytest.raises(TypeError):
            GroqClient()  # type: ignore[call-arg]


@pytest.mark.unit
class TestGroqClientMessageConversion:
    """Test message conversion to Groq format."""

    def test_convert_system_message(self) -> None:
        """System messages should convert correctly."""
        client = GroqClient(api_key="test-key")
        messages = [Message(role=Role.SYSTEM, content="You are helpful.")]

        result = client._convert_messages(messages)

        assert len(result) == 1
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are helpful."

    def test_convert_user_message(self) -> None:
        """User messages should convert correctly."""
        client = GroqClient(api_key="test-key")
        messages = [Message(role=Role.USER, content="Hello")]

        result = client._convert_messages(messages)

        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "Hello"

    def test_convert_assistant_message(self) -> None:
        """Assistant messages should convert correctly."""
        client = GroqClient(api_key="test-key")
        messages = [Message(role=Role.ASSISTANT, content="Hi there!")]

        result = client._convert_messages(messages)

        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "Hi there!"


@pytest.mark.unit
class TestGroqClientToolConversion:
    """Test tool conversion to Groq format."""

    def test_convert_tool_definition(self) -> None:
        """Tool definitions should convert correctly."""
        client = GroqClient(api_key="test-key")
        tools = [
            ToolDefinition(
                name=ToolName("search"),
                description="Search for information",
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            )
        ]

        result = client._convert_tools(tools)

        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "search"
        assert result[0]["function"]["description"] == "Search for information"
        assert "properties" in result[0]["function"]["parameters"]


@pytest.mark.unit
class TestGroqClientToolCallError:
    """Test tool call error detection."""

    def test_is_tool_call_error_true(self) -> None:
        """Should detect tool_use_failed error code."""
        client = GroqClient(api_key="test-key")

        error = BadRequestError(
            message="Tool use failed",
            body={"error": {"code": "tool_use_failed", "message": "Failed"}},
            response=MagicMock(),
        )

        assert client._is_tool_call_error(error) is True

    def test_is_tool_call_error_false_different_code(self) -> None:
        """Should return False for other error codes."""
        client = GroqClient(api_key="test-key")

        error = BadRequestError(
            message="Invalid request",
            body={"error": {"code": "invalid_request", "message": "Bad request"}},
            response=MagicMock(),
        )

        assert client._is_tool_call_error(error) is False

    def test_is_tool_call_error_false_no_body(self) -> None:
        """Should return False when body is None."""
        client = GroqClient(api_key="test-key")

        error = BadRequestError(
            message="Error",
            body=None,
            response=MagicMock(),
        )

        assert client._is_tool_call_error(error) is False

    def test_is_tool_call_error_false_non_dict_error(self) -> None:
        """Should return False when error field is not a dict."""
        client = GroqClient(api_key="test-key")

        error = BadRequestError(
            message="Error",
            body={"error": "string error not dict"},
            response=MagicMock(),
        )

        assert client._is_tool_call_error(error) is False

    def test_is_tool_call_error_false_missing_error_field(self) -> None:
        """Should return False when error field is missing."""
        client = GroqClient(api_key="test-key")

        error = BadRequestError(
            message="Error",
            body={"message": "some error"},
            response=MagicMock(),
        )

        assert client._is_tool_call_error(error) is False


@pytest.mark.unit
class TestGroqClientRetry:
    """Test retry logic for tool call failures."""

    @pytest.mark.asyncio
    async def test_retry_on_tool_call_failure(self) -> None:
        """Should retry with lower temperature on tool_use_failed."""
        client = GroqClient(api_key="test-key")

        # Mock the internal client
        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        # First call fails, second succeeds
        tool_error = BadRequestError(
            message="Tool use failed",
            body={"error": {"code": "tool_use_failed", "message": "Failed"}},
            response=MagicMock(),
        )

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Success"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.model = "test-model"

        mock_create.side_effect = [tool_error, mock_response]

        tools = [
            ToolDefinition(
                name=ToolName("test"),
                description="Test tool",
                parameters={"type": "object", "properties": {}},
            )
        ]

        result = await client.complete(
            messages=[Message(role=Role.USER, content="Hi")],
            model=Model.GROQ_GPT_OSS_20B,
            tools=tools,
        )

        assert result.message.content == "Success"
        assert mock_create.call_count == 2

        # First call with default temperature
        first_call = mock_create.call_args_list[0]
        assert first_call.kwargs["temperature"] == LLMDefaults.TEMPERATURE

        # Second call with retry temperature
        second_call = mock_create.call_args_list[1]
        assert second_call.kwargs["temperature"] == LLMDefaults.RETRY_TEMPERATURE

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self) -> None:
        """Should raise ToolCallGenerationError after max retries."""
        client = GroqClient(api_key="test-key")

        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        tool_error = BadRequestError(
            message="Tool use failed",
            body={"error": {"code": "tool_use_failed", "message": "Failed"}},
            response=MagicMock(),
        )

        # Always fail
        mock_create.side_effect = tool_error

        tools = [
            ToolDefinition(
                name=ToolName("test"),
                description="Test tool",
                parameters={"type": "object", "properties": {}},
            )
        ]

        with pytest.raises(ToolCallGenerationError) as exc_info:
            await client.complete(
                messages=[Message(role=Role.USER, content="Hi")],
                model=Model.GROQ_GPT_OSS_20B,
                tools=tools,
            )

        assert exc_info.value.retries == LLMDefaults.MAX_TOOL_CALL_RETRIES
        # Should have tried initial + retries
        assert mock_create.call_count == LLMDefaults.MAX_TOOL_CALL_RETRIES + 1

    @pytest.mark.asyncio
    async def test_no_retry_without_tools(self) -> None:
        """Should not retry tool errors when no tools provided."""
        client = GroqClient(api_key="test-key")

        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        tool_error = BadRequestError(
            message="Tool use failed",
            body={"error": {"code": "tool_use_failed", "message": "Failed"}},
            response=MagicMock(),
        )

        mock_create.side_effect = tool_error

        # No tools provided - should re-raise immediately
        with pytest.raises(ProviderError):
            await client.complete(
                messages=[Message(role=Role.USER, content="Hi")],
                model=Model.GROQ_GPT_OSS_20B,
                tools=None,
            )

        assert mock_create.call_count == 1

    @pytest.mark.asyncio
    async def test_custom_temperature_used(self) -> None:
        """Should use custom temperature when provided."""
        client = GroqClient(api_key="test-key")

        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.model = "test-model"

        mock_create.return_value = mock_response

        await client.complete(
            messages=[Message(role=Role.USER, content="Hi")],
            model=Model.GROQ_GPT_OSS_20B,
            temperature=0.5,
        )

        mock_create.assert_called_once()
        assert mock_create.call_args.kwargs["temperature"] == 0.5

    @pytest.mark.asyncio
    async def test_non_tool_error_reraises_immediately(self) -> None:
        """Should re-raise non-tool BadRequestError without retry."""
        client = GroqClient(api_key="test-key")

        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        # Different error code - not a tool call error
        other_error = BadRequestError(
            message="Invalid request",
            body={"error": {"code": "invalid_request", "message": "Bad"}},
            response=MagicMock(),
        )

        mock_create.side_effect = other_error

        tools = [
            ToolDefinition(
                name=ToolName("test"),
                description="Test tool",
                parameters={"type": "object", "properties": {}},
            )
        ]

        # Should re-raise immediately without retry
        with pytest.raises(ProviderError):
            await client.complete(
                messages=[Message(role=Role.USER, content="Hi")],
                model=Model.GROQ_GPT_OSS_20B,
                tools=tools,
            )

        # Only one attempt - no retries for non-tool errors
        assert mock_create.call_count == 1


@pytest.mark.unit
class TestGroqClientReasoningEffort:
    """Test reasoning_effort parameter handling."""

    @pytest.mark.asyncio
    async def test_reasoning_effort_passed_to_gpt_oss_model(self) -> None:
        """reasoning_effort should be passed for GPT-OSS models."""
        client = GroqClient(api_key="test-key")

        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Reasoning response"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.model = "openai/gpt-oss-20b"

        mock_create.return_value = mock_response

        await client.complete(
            messages=[Message(role=Role.USER, content="Think carefully about this")],
            model=Model.GROQ_GPT_OSS_20B,
            reasoning_effort=ReasoningEffort.HIGH,
        )

        mock_create.assert_called_once()
        assert mock_create.call_args.kwargs["reasoning_effort"] == "high"

    @pytest.mark.asyncio
    async def test_reasoning_effort_raises_for_non_reasoning_model(self) -> None:
        """reasoning_effort should raise for non-GPT-OSS models."""
        client = GroqClient(api_key="test-key")

        with pytest.raises(UnsupportedParameterError) as exc_info:
            await client.complete(
                messages=[Message(role=Role.USER, content="Hi")],
                model=Model.GROQ_QWEN3_6_27B,
                reasoning_effort=ReasoningEffort.HIGH,
            )

        assert "qwen/qwen3.6-27b" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_reasoning_effort_none_passed_as_none(self) -> None:
        """reasoning_effort=None should pass None to API."""
        client = GroqClient(api_key="test-key")

        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Normal response"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.model = "openai/gpt-oss-20b"

        mock_create.return_value = mock_response

        await client.complete(
            messages=[Message(role=Role.USER, content="Hi")],
            model=Model.GROQ_GPT_OSS_20B,
            reasoning_effort=None,
        )

        mock_create.assert_called_once()
        assert mock_create.call_args.kwargs["reasoning_effort"] is None

    @pytest.mark.asyncio
    async def test_reasoning_effort_in_stream(self) -> None:
        """reasoning_effort should work with stream() method."""
        client = GroqClient(api_key="test-key")

        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        # Mock async iterator
        class MockAsyncIterator:
            def __init__(self) -> None:
                self.items: list[object] = []
                self.index = 0

            def __aiter__(self) -> "MockAsyncIterator":
                return self

            async def __anext__(self) -> object:
                if self.index >= len(self.items):
                    raise StopAsyncIteration
                item = self.items[self.index]
                self.index += 1
                return item

        mock_create.return_value = MockAsyncIterator()

        # Consume the generator (even though empty)
        async for _ in client.stream(
            messages=[Message(role=Role.USER, content="Think")],
            model=Model.GROQ_GPT_OSS_20B,
            reasoning_effort=ReasoningEffort.MEDIUM,
        ):
            pass

        mock_create.assert_called_once()
        assert mock_create.call_args.kwargs["reasoning_effort"] == "medium"

    @pytest.mark.asyncio
    async def test_reasoning_effort_stream_raises_for_non_reasoning_model(self) -> None:
        """reasoning_effort in stream() should raise for non-GPT-OSS models."""
        client = GroqClient(api_key="test-key")

        with pytest.raises(UnsupportedParameterError):
            async for _ in client.stream(
                messages=[Message(role=Role.USER, content="Hi")],
                model=Model.GROQ_QWEN3_6_27B,
                reasoning_effort=ReasoningEffort.LOW,
            ):
                pass

    @pytest.mark.asyncio
    async def test_reasoning_effort_max_downgraded_to_high(self) -> None:
        """reasoning_effort MAX should be downgraded to HIGH for Groq models."""
        client = GroqClient(api_key="test-key")

        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Response"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.model = "openai/gpt-oss-20b"

        mock_create.return_value = mock_response

        await client.complete(
            messages=[Message(role=Role.USER, content="Think")],
            model=Model.GROQ_GPT_OSS_20B,
            reasoning_effort=ReasoningEffort.MAX,
        )

        mock_create.assert_called_once()
        assert mock_create.call_args.kwargs["reasoning_effort"] == "high"

    @pytest.mark.asyncio
    async def test_reasoning_effort_max_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A warning should be logged when MAX is downgraded to HIGH."""
        import logging

        client = GroqClient(api_key="test-key")

        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Response"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.model = "openai/gpt-oss-20b"

        mock_create.return_value = mock_response

        with caplog.at_level(logging.WARNING, logger="neosian._foundation.llm.groq"):
            await client.complete(
                messages=[Message(role=Role.USER, content="Think")],
                model=Model.GROQ_GPT_OSS_20B,
                reasoning_effort=ReasoningEffort.MAX,
            )

        assert any("MAX" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_reasoning_effort_max_in_stream_downgraded(self) -> None:
        """reasoning_effort MAX in stream() should be downgraded to HIGH."""
        client = GroqClient(api_key="test-key")

        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        # Mock async iterator
        class MockAsyncIterator:
            def __init__(self) -> None:
                self.items: list[object] = []
                self.index = 0

            def __aiter__(self) -> "MockAsyncIterator":
                return self

            async def __anext__(self) -> object:
                if self.index >= len(self.items):
                    raise StopAsyncIteration
                item = self.items[self.index]
                self.index += 1
                return item

        mock_create.return_value = MockAsyncIterator()

        async for _ in client.stream(
            messages=[Message(role=Role.USER, content="Think")],
            model=Model.GROQ_GPT_OSS_20B,
            reasoning_effort=ReasoningEffort.MAX,
        ):
            pass

        mock_create.assert_called_once()
        assert mock_create.call_args.kwargs["reasoning_effort"] == "high"


@pytest.mark.unit
class TestGroqClientReasoningContent:
    """Test reasoning content parsing."""

    @pytest.mark.asyncio
    async def test_complete_parses_reasoning_content(self) -> None:
        """complete() should parse reasoning from response message."""
        client = GroqClient(api_key="test-key")

        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "The answer is 42"
        mock_response.choices[0].message.reasoning = "Let me think about this..."
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 20
        mock_response.model = "openai/gpt-oss-20b"

        mock_create.return_value = mock_response

        result = await client.complete(
            messages=[Message(role=Role.USER, content="What is the meaning of life?")],
            model=Model.GROQ_GPT_OSS_20B,
        )

        assert result.message.content == "The answer is 42"
        assert result.message.reasoning == "Let me think about this..."

    @pytest.mark.asyncio
    async def test_complete_handles_no_reasoning(self) -> None:
        """complete() should handle responses without reasoning field."""
        client = GroqClient(api_key="test-key")

        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello"
        # No reasoning attribute on mock - getattr will return None
        del mock_response.choices[0].message.reasoning
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage.prompt_tokens = 5
        mock_response.usage.completion_tokens = 2
        mock_response.model = "llama-3.3-70b-versatile"

        mock_create.return_value = mock_response

        result = await client.complete(
            messages=[Message(role=Role.USER, content="Hi")],
            model=Model.GROQ_QWEN3_6_27B,
        )

        assert result.message.content == "Hello"
        assert result.message.reasoning is None

    @pytest.mark.asyncio
    async def test_stream_parses_reasoning_chunks(self) -> None:
        """stream() should parse reasoning from delta."""
        client = GroqClient(api_key="test-key")

        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        class MockAsyncIterator:
            def __init__(self, items: list[object]) -> None:
                self.items = items
                self.index = 0

            def __aiter__(self) -> "MockAsyncIterator":
                return self

            async def __anext__(self) -> object:
                if self.index >= len(self.items):
                    raise StopAsyncIteration
                item = self.items[self.index]
                self.index += 1
                return item

        # Create mock chunks with reasoning
        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        chunk1.choices[0].delta.content = None
        chunk1.choices[0].delta.reasoning = "Let me think"
        chunk1.choices[0].delta.tool_calls = None
        chunk1.choices[0].finish_reason = None
        chunk1.usage = None

        chunk2 = MagicMock()
        chunk2.choices = [MagicMock()]
        chunk2.choices[0].delta.content = "Answer"
        chunk2.choices[0].delta.reasoning = None
        chunk2.choices[0].delta.tool_calls = None
        chunk2.choices[0].finish_reason = None
        chunk2.usage = None

        chunk3 = MagicMock()
        chunk3.choices = [MagicMock()]
        chunk3.choices[0].delta.content = None
        chunk3.choices[0].delta.reasoning = None
        chunk3.choices[0].delta.tool_calls = None
        chunk3.choices[0].finish_reason = "stop"
        chunk3.usage = None

        mock_create.return_value = MockAsyncIterator([chunk1, chunk2, chunk3])

        chunks = []
        async for chunk in client.stream(
            messages=[Message(role=Role.USER, content="Think")],
            model=Model.GROQ_GPT_OSS_20B,
        ):
            chunks.append(chunk)

        assert len(chunks) == 3
        # First chunk has reasoning
        assert chunks[0].reasoning == "Let me think"
        assert chunks[0].content is None
        # Second chunk has content
        assert chunks[1].content == "Answer"
        assert chunks[1].reasoning is None
        # Third chunk has finish_reason
        assert chunks[2].finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_stream_chunks_carry_api_model(self) -> None:
        """Every chunk carries the API-reported model, usage-only included."""
        client = GroqClient(api_key="test-key")

        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        class MockAsyncIterator:
            def __init__(self, items: list[object]) -> None:
                self.items = items
                self.index = 0

            def __aiter__(self) -> "MockAsyncIterator":
                return self

            async def __anext__(self) -> object:
                if self.index >= len(self.items):
                    raise StopAsyncIteration
                item = self.items[self.index]
                self.index += 1
                return item

        content_chunk = MagicMock()
        content_chunk.model = "openai/gpt-oss-20b-live"
        content_chunk.choices = [MagicMock()]
        content_chunk.choices[0].delta.content = "Hi"
        content_chunk.choices[0].delta.reasoning = None
        content_chunk.choices[0].delta.tool_calls = None
        content_chunk.choices[0].finish_reason = "stop"
        content_chunk.usage = None

        usage_chunk = MagicMock()
        usage_chunk.model = "openai/gpt-oss-20b-live"
        usage_chunk.choices = []
        usage_chunk.usage = MagicMock(prompt_tokens=10, completion_tokens=5)

        mock_create.return_value = MockAsyncIterator([content_chunk, usage_chunk])

        chunks = []
        async for chunk in client.stream(
            messages=[Message(role=Role.USER, content="Hi")],
            model=Model.GROQ_GPT_OSS_20B,
        ):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert all(c.model == "openai/gpt-oss-20b-live" for c in chunks)

    @pytest.mark.asyncio
    async def test_stream_handles_no_reasoning_attribute(self) -> None:
        """stream() should handle deltas without reasoning attribute."""
        client = GroqClient(api_key="test-key")

        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        class MockAsyncIterator:
            def __init__(self, items: list[object]) -> None:
                self.items = items
                self.index = 0

            def __aiter__(self) -> "MockAsyncIterator":
                return self

            async def __anext__(self) -> object:
                if self.index >= len(self.items):
                    raise StopAsyncIteration
                item = self.items[self.index]
                self.index += 1
                return item

        # Create mock chunk without reasoning attribute
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = "Hello"
        # No reasoning attribute - getattr will return None
        del chunk.choices[0].delta.reasoning
        chunk.choices[0].delta.tool_calls = None
        chunk.choices[0].finish_reason = "stop"
        chunk.usage = None

        mock_create.return_value = MockAsyncIterator([chunk])

        chunks = []
        async for c in client.stream(
            messages=[Message(role=Role.USER, content="Hi")],
            model=Model.GROQ_QWEN3_6_27B,
        ):
            chunks.append(c)

        assert len(chunks) == 1
        assert chunks[0].content == "Hello"
        assert chunks[0].reasoning is None


@pytest.mark.unit
class TestGroqMultimodalRejected:
    """Groq's converter is text-only — block content must fail loudly."""

    def test_convert_messages_block_content_raises(self) -> None:
        from neosian._foundation.llm.base import ImageBlock
        from neosian._foundation.shared.exceptions import UnsupportedContentError

        client = GroqClient(api_key="test-key")
        messages = [
            Message(
                role=Role.USER,
                content=[ImageBlock(media_type="image/png", data="aGVsbG8=")],
            ),
        ]

        with pytest.raises(UnsupportedContentError, match="groq"):
            client._convert_messages(messages)


@pytest.mark.unit
class TestGroqNestedSchema:
    """Groq strict mode requires additionalProperties on every object."""

    def test_nested_model_defs_carry_additional_properties(self) -> None:
        from pydantic import BaseModel

        from neosian._foundation.shared.types import ResponseFormat

        class Question(BaseModel):
            question: str
            options: list[str]

        class Quiz(BaseModel):
            questions: list[Question]

        client = GroqClient(api_key="test-key")
        payload = client._convert_response_format(ResponseFormat(schema=Quiz))
        schema = payload["json_schema"]["schema"]  # type: ignore[index]

        assert schema["additionalProperties"] is False
        assert schema["$defs"]["Question"]["additionalProperties"] is False
