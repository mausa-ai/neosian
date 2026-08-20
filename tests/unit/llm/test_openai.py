"""Tests for OpenAI LLM client."""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from openai import BadRequestError

from neosian._foundation.llm.base import (
    CompactionBlock,
    Message,
    Role,
    ToolDefinition,
)
from neosian._foundation.llm.openai import OpenAIClient
from neosian._foundation.shared.constants import LLMDefaults
from neosian._foundation.shared.exceptions import (
    ProviderError,
    ToolCallGenerationError,
    UnsupportedContentError,
    UnsupportedParameterError,
)
from neosian._foundation.shared.types import Model, ReasoningEffort, ToolName


def _sdk(client: OpenAIClient) -> Any:
    """The underlying SDK client, untyped for mock wiring and inspection."""
    return client._client


@pytest.mark.unit
class TestOpenAIClientInit:
    """Test OpenAIClient initialization."""

    def test_requires_api_key(self) -> None:
        """OpenAIClient should require an explicit API key."""
        # This should work - explicit key
        client = OpenAIClient(api_key="test-key")
        assert client is not None

    def test_api_key_is_required_parameter(self) -> None:
        """OpenAIClient should not accept None as api_key."""
        # The type signature enforces str, not str | None
        # This test documents the expected behavior
        with pytest.raises(TypeError):
            OpenAIClient()  # type: ignore[call-arg]


@pytest.mark.unit
class TestOpenAIClientMessageConversion:
    """Test message conversion to OpenAI format."""

    def test_convert_system_message(self) -> None:
        """System messages should convert correctly."""
        client = OpenAIClient(api_key="test-key")
        messages = [Message(role=Role.SYSTEM, content="You are helpful.")]

        result = client._convert_messages(messages)

        assert len(result) == 1
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are helpful."

    def test_convert_user_message(self) -> None:
        """User messages should convert correctly."""
        client = OpenAIClient(api_key="test-key")
        messages = [Message(role=Role.USER, content="Hello")]

        result = client._convert_messages(messages)

        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "Hello"

    def test_convert_assistant_message(self) -> None:
        """Assistant messages should convert correctly."""
        client = OpenAIClient(api_key="test-key")
        messages = [Message(role=Role.ASSISTANT, content="Hi there!")]

        result = client._convert_messages(messages)

        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "Hi there!"


@pytest.mark.unit
class TestOpenAIClientToolConversion:
    """Test tool conversion to OpenAI format."""

    def test_convert_tool_definition(self) -> None:
        """Tool definitions should convert correctly."""
        client = OpenAIClient(api_key="test-key")
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
class TestOpenAIClientToolCallError:
    """Test tool call error detection."""

    def test_is_tool_call_error_true_invalid_tool_call(self) -> None:
        """Should detect invalid_tool_call error code."""
        client = OpenAIClient(api_key="test-key")

        error = BadRequestError(
            message="Invalid tool call",
            body={"error": {"code": "invalid_tool_call", "message": "Failed"}},
            response=MagicMock(),
        )

        assert client._is_tool_call_error(error) is True

    def test_is_tool_call_error_true_tool_in_message(self) -> None:
        """Should detect tool-related errors by message content."""
        client = OpenAIClient(api_key="test-key")

        error = BadRequestError(
            message="Tool error",
            body={"error": {"code": "some_code", "message": "Invalid tool arguments"}},
            response=MagicMock(),
        )

        assert client._is_tool_call_error(error) is True

    def test_is_tool_call_error_true_function_in_message(self) -> None:
        """Should detect function-related errors by message content."""
        client = OpenAIClient(api_key="test-key")

        error = BadRequestError(
            message="Function error",
            body={"error": {"code": "some_code", "message": "Invalid function call"}},
            response=MagicMock(),
        )

        assert client._is_tool_call_error(error) is True

    def test_is_tool_call_error_false_different_code(self) -> None:
        """Should return False for other error codes."""
        client = OpenAIClient(api_key="test-key")

        error = BadRequestError(
            message="Invalid request",
            body={"error": {"code": "invalid_request", "message": "Bad request"}},
            response=MagicMock(),
        )

        assert client._is_tool_call_error(error) is False

    def test_is_tool_call_error_false_no_body(self) -> None:
        """Should return False when body is None."""
        client = OpenAIClient(api_key="test-key")

        error = BadRequestError(
            message="Error",
            body=None,
            response=MagicMock(),
        )

        assert client._is_tool_call_error(error) is False

    def test_is_tool_call_error_false_non_dict_error(self) -> None:
        """Should return False when error field is not a dict."""
        client = OpenAIClient(api_key="test-key")

        error = BadRequestError(
            message="Error",
            body={"error": "string error not dict"},
            response=MagicMock(),
        )

        assert client._is_tool_call_error(error) is False

    def test_is_tool_call_error_false_missing_error_field(self) -> None:
        """Should return False when error field is missing."""
        client = OpenAIClient(api_key="test-key")

        error = BadRequestError(
            message="Error",
            body={"message": "some error"},
            response=MagicMock(),
        )

        assert client._is_tool_call_error(error) is False


@pytest.mark.unit
class TestOpenAIClientRetry:
    """Test retry logic for tool call failures."""

    @pytest.mark.asyncio
    async def test_retry_on_tool_call_failure(self) -> None:
        """Should retry on tool call failure."""
        client = OpenAIClient(api_key="test-key")

        # Mock the internal client
        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        # First call fails, second succeeds
        tool_error = BadRequestError(
            message="Invalid tool call",
            body={"error": {"code": "invalid_tool_call", "message": "Failed"}},
            response=MagicMock(),
        )

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Success"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.model = "gpt-4o-mini"

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
            model=Model.GPT_5_MINI,
            tools=tools,
        )

        assert result.message.content == "Success"
        assert mock_create.call_count == 2

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self) -> None:
        """Should raise ToolCallGenerationError after max retries."""
        client = OpenAIClient(api_key="test-key")

        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        tool_error = BadRequestError(
            message="Invalid tool call",
            body={"error": {"code": "invalid_tool_call", "message": "Failed"}},
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
                model=Model.GPT_5_MINI,
                tools=tools,
            )

        assert exc_info.value.retries == LLMDefaults.MAX_TOOL_CALL_RETRIES
        # Should have tried initial + retries
        assert mock_create.call_count == LLMDefaults.MAX_TOOL_CALL_RETRIES + 1

    @pytest.mark.asyncio
    async def test_no_retry_without_tools(self) -> None:
        """Should not retry tool errors when no tools provided."""
        client = OpenAIClient(api_key="test-key")

        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        tool_error = BadRequestError(
            message="Invalid tool call",
            body={"error": {"code": "invalid_tool_call", "message": "Failed"}},
            response=MagicMock(),
        )

        mock_create.side_effect = tool_error

        # No tools provided - should re-raise immediately
        with pytest.raises(ProviderError):
            await client.complete(
                messages=[Message(role=Role.USER, content="Hi")],
                model=Model.GPT_5_MINI,
                tools=None,
            )

        assert mock_create.call_count == 1

    @pytest.mark.asyncio
    async def test_non_tool_error_reraises_immediately(self) -> None:
        """Should re-raise non-tool BadRequestError without retry."""
        client = OpenAIClient(api_key="test-key")

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
                model=Model.GPT_5_MINI,
                tools=tools,
            )

        # Only one attempt - no retries for non-tool errors
        assert mock_create.call_count == 1


@pytest.mark.unit
class TestOpenAIClientTemperature:
    """Test temperature parameter handling."""

    @pytest.mark.asyncio
    async def test_temperature_raises_error(self) -> None:
        """Should raise UnsupportedParameterError when temperature is provided."""
        client = OpenAIClient(api_key="test-key")

        with pytest.raises(UnsupportedParameterError) as exc_info:
            await client.complete(
                messages=[Message(role=Role.USER, content="Hi")],
                model=Model.GPT_5_NANO,
                temperature=0.5,
            )

        assert "temperature" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_no_temperature_works(self) -> None:
        """Should work when temperature is not provided."""
        client = OpenAIClient(api_key="test-key")

        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.model = "gpt-5-nano"

        mock_create.return_value = mock_response

        result = await client.complete(
            messages=[Message(role=Role.USER, content="Hi")],
            model=Model.GPT_5_NANO,
        )

        assert result.message.content == "Hello"
        mock_create.assert_called_once()


@pytest.mark.unit
class TestOpenAIClientReasoningEffort:
    """Test reasoning_effort parameter handling for OpenAI GPT-5 models."""

    def _mock_response(self, model: str = "gpt-5-nano-2025-08-07") -> MagicMock:
        """Create a mock OpenAI response."""
        mock = MagicMock()
        mock.choices = [MagicMock()]
        mock.choices[0].message.content = "Response"
        mock.choices[0].message.tool_calls = None
        mock.usage.prompt_tokens = 10
        mock.usage.completion_tokens = 5
        mock.model = model
        return mock

    @pytest.mark.asyncio
    async def test_reasoning_effort_passed_to_api(self) -> None:
        """reasoning_effort HIGH should be passed to the API."""
        client = OpenAIClient(api_key="test-key")
        mock_create = AsyncMock(return_value=self._mock_response())
        _sdk(client).chat.completions.create = mock_create

        await client.complete(
            messages=[Message(role=Role.USER, content="Think carefully")],
            model=Model.GPT_5_NANO,
            reasoning_effort=ReasoningEffort.HIGH,
        )

        mock_create.assert_called_once()
        assert mock_create.call_args.kwargs["reasoning_effort"] == "high"

    @pytest.mark.asyncio
    async def test_reasoning_effort_low_passed(self) -> None:
        """reasoning_effort LOW should be passed for GPT-5 models."""
        client = OpenAIClient(api_key="test-key")
        mock_create = AsyncMock(return_value=self._mock_response())
        _sdk(client).chat.completions.create = mock_create

        await client.complete(
            messages=[Message(role=Role.USER, content="Hi")],
            model=Model.GPT_5_NANO,
            reasoning_effort=ReasoningEffort.LOW,
        )

        assert mock_create.call_args.kwargs["reasoning_effort"] == "low"

    @pytest.mark.asyncio
    async def test_reasoning_effort_none_uses_omit(self) -> None:
        """reasoning_effort=None should pass the omit sentinel to the API."""
        from openai import omit

        client = OpenAIClient(api_key="test-key")
        mock_create = AsyncMock(return_value=self._mock_response())
        _sdk(client).chat.completions.create = mock_create

        await client.complete(
            messages=[Message(role=Role.USER, content="Hi")],
            model=Model.GPT_5_NANO,
        )

        assert mock_create.call_args.kwargs["reasoning_effort"] is omit

    @pytest.mark.asyncio
    async def test_reasoning_effort_max_downgraded_to_high(self) -> None:
        """reasoning_effort MAX should be downgraded to HIGH for OpenAI models."""
        client = OpenAIClient(api_key="test-key")
        mock_create = AsyncMock(return_value=self._mock_response())
        _sdk(client).chat.completions.create = mock_create

        await client.complete(
            messages=[Message(role=Role.USER, content="Think")],
            model=Model.GPT_5_NANO,
            reasoning_effort=ReasoningEffort.MAX,
        )

        assert mock_create.call_args.kwargs["reasoning_effort"] == "high"

    @pytest.mark.asyncio
    async def test_reasoning_effort_max_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A warning should be logged when MAX is downgraded to HIGH."""
        import logging

        client = OpenAIClient(api_key="test-key")
        mock_create = AsyncMock(return_value=self._mock_response())
        _sdk(client).chat.completions.create = mock_create

        with caplog.at_level(logging.WARNING, logger="neosian._foundation.llm.openai"):
            await client.complete(
                messages=[Message(role=Role.USER, content="Think")],
                model=Model.GPT_5_NANO,
                reasoning_effort=ReasoningEffort.MAX,
            )

        assert any("MAX" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_gpt5_pro_forces_high_from_low(self) -> None:
        """GPT-5 Pro should force LOW to HIGH."""
        client = OpenAIClient(api_key="test-key")
        mock_create = AsyncMock(
            return_value=self._mock_response("gpt-5-pro-2025-10-06")
        )
        _sdk(client).chat.completions.create = mock_create

        await client.complete(
            messages=[Message(role=Role.USER, content="Think")],
            model=Model.GPT_5_PRO,
            reasoning_effort=ReasoningEffort.LOW,
        )

        assert mock_create.call_args.kwargs["reasoning_effort"] == "high"

    @pytest.mark.asyncio
    async def test_gpt5_pro_forces_high_from_medium(self) -> None:
        """GPT-5 Pro should force MEDIUM to HIGH."""
        client = OpenAIClient(api_key="test-key")
        mock_create = AsyncMock(
            return_value=self._mock_response("gpt-5-pro-2025-10-06")
        )
        _sdk(client).chat.completions.create = mock_create

        await client.complete(
            messages=[Message(role=Role.USER, content="Think")],
            model=Model.GPT_5_PRO,
            reasoning_effort=ReasoningEffort.MEDIUM,
        )

        assert mock_create.call_args.kwargs["reasoning_effort"] == "high"

    @pytest.mark.asyncio
    async def test_gpt5_pro_high_passes_through(self) -> None:
        """GPT-5 Pro with HIGH should pass through correctly."""
        client = OpenAIClient(api_key="test-key")
        mock_create = AsyncMock(
            return_value=self._mock_response("gpt-5-pro-2025-10-06")
        )
        _sdk(client).chat.completions.create = mock_create

        await client.complete(
            messages=[Message(role=Role.USER, content="Think")],
            model=Model.GPT_5_PRO,
            reasoning_effort=ReasoningEffort.HIGH,
        )

        assert mock_create.call_args.kwargs["reasoning_effort"] == "high"

    @pytest.mark.asyncio
    async def test_gpt5_pro_forced_high_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Warning should be logged when GPT-5-Pro forces HIGH."""
        import logging

        client = OpenAIClient(api_key="test-key")
        mock_create = AsyncMock(
            return_value=self._mock_response("gpt-5-pro-2025-10-06")
        )
        _sdk(client).chat.completions.create = mock_create

        with caplog.at_level(logging.WARNING, logger="neosian._foundation.llm.openai"):
            await client.complete(
                messages=[Message(role=Role.USER, content="Think")],
                model=Model.GPT_5_PRO,
                reasoning_effort=ReasoningEffort.LOW,
            )

        assert any("HIGH" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_reasoning_effort_in_stream(self) -> None:
        """reasoning_effort should be passed in stream() method."""
        client = OpenAIClient(api_key="test-key")
        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        async def empty_stream() -> None:
            return

        mock_iterator = MagicMock()
        mock_iterator.__aiter__ = MagicMock(return_value=iter([]))

        # Create a proper async iterator
        class EmptyAsyncIter:
            def __aiter__(self) -> "EmptyAsyncIter":
                return self

            async def __anext__(self) -> None:
                raise StopAsyncIteration

        mock_create.return_value = EmptyAsyncIter()

        async for _ in client.stream(
            messages=[Message(role=Role.USER, content="Think")],
            model=Model.GPT_5_NANO,
            reasoning_effort=ReasoningEffort.MEDIUM,
        ):
            pass

        mock_create.assert_called_once()
        assert mock_create.call_args.kwargs["reasoning_effort"] == "medium"

    @pytest.mark.asyncio
    async def test_reasoning_effort_max_in_stream_downgraded(self) -> None:
        """reasoning_effort MAX in stream() should be downgraded to HIGH."""
        client = OpenAIClient(api_key="test-key")
        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        class EmptyAsyncIter:
            def __aiter__(self) -> "EmptyAsyncIter":
                return self

            async def __anext__(self) -> None:
                raise StopAsyncIteration

        mock_create.return_value = EmptyAsyncIter()

        async for _ in client.stream(
            messages=[Message(role=Role.USER, content="Think")],
            model=Model.GPT_5_NANO,
            reasoning_effort=ReasoningEffort.MAX,
        ):
            pass

        assert mock_create.call_args.kwargs["reasoning_effort"] == "high"


@pytest.mark.unit
class TestOpenAIPromptCaching:
    """Tests for OpenAI automatic prompt caching token extraction."""

    def _mock_response(
        self,
        prompt_tokens: int = 1000,
        completion_tokens: int = 50,
        cached_tokens: int | None = None,
        model: str = "gpt-5-nano-2025-08-07",
    ) -> MagicMock:
        """Create a mock OpenAI response with optional cache details."""
        mock = MagicMock()
        mock.choices = [MagicMock()]
        mock.choices[0].message.content = "Hello"
        mock.choices[0].message.tool_calls = None
        mock.usage.prompt_tokens = prompt_tokens
        mock.usage.completion_tokens = completion_tokens

        if cached_tokens is not None:
            mock.usage.prompt_tokens_details = MagicMock()
            mock.usage.prompt_tokens_details.cached_tokens = cached_tokens
        else:
            mock.usage.prompt_tokens_details = None

        mock.model = model
        return mock

    @pytest.mark.asyncio
    async def test_parse_response_extracts_cached_tokens(self) -> None:
        """Should extract cached tokens and normalize input_tokens."""
        client = OpenAIClient(api_key="test-key")
        mock_create = AsyncMock(
            return_value=self._mock_response(
                prompt_tokens=1000, completion_tokens=50, cached_tokens=800
            )
        )
        _sdk(client).chat.completions.create = mock_create

        response = await client.complete(
            messages=[Message(role=Role.USER, content="Hi")],
            model=Model.GPT_5_NANO,
        )

        # input_tokens should be normalized: prompt_tokens - cached_tokens
        assert response.usage.input_tokens == 200
        assert response.usage.cache_read_tokens == 800
        assert response.usage.cache_write_tokens == 0
        assert response.usage.output_tokens == 50

    @pytest.mark.asyncio
    async def test_parse_response_no_cache_details(self) -> None:
        """Should handle None prompt_tokens_details gracefully."""
        client = OpenAIClient(api_key="test-key")
        mock_create = AsyncMock(
            return_value=self._mock_response(
                prompt_tokens=500, completion_tokens=20, cached_tokens=None
            )
        )
        _sdk(client).chat.completions.create = mock_create

        response = await client.complete(
            messages=[Message(role=Role.USER, content="Hi")],
            model=Model.GPT_5_NANO,
        )

        assert response.usage.input_tokens == 500
        assert response.usage.cache_read_tokens == 0

    @pytest.mark.asyncio
    async def test_parse_response_cached_tokens_none(self) -> None:
        """Should handle cached_tokens=None in prompt_tokens_details."""
        client = OpenAIClient(api_key="test-key")
        mock_resp = self._mock_response(prompt_tokens=500, completion_tokens=20)
        mock_resp.usage.prompt_tokens_details = MagicMock()
        mock_resp.usage.prompt_tokens_details.cached_tokens = None
        mock_create = AsyncMock(return_value=mock_resp)
        _sdk(client).chat.completions.create = mock_create

        response = await client.complete(
            messages=[Message(role=Role.USER, content="Hi")],
            model=Model.GPT_5_NANO,
        )

        assert response.usage.input_tokens == 500
        assert response.usage.cache_read_tokens == 0

    @pytest.mark.asyncio
    async def test_parse_response_total_tokens_correct_with_cache(self) -> None:
        """total_tokens should equal prompt_tokens + completion_tokens after normalization."""
        client = OpenAIClient(api_key="test-key")
        mock_create = AsyncMock(
            return_value=self._mock_response(
                prompt_tokens=1000, completion_tokens=50, cached_tokens=600
            )
        )
        _sdk(client).chat.completions.create = mock_create

        response = await client.complete(
            messages=[Message(role=Role.USER, content="Hi")],
            model=Model.GPT_5_NANO,
        )

        # total = (1000 - 600) + 50 + 0 + 600 = 1050
        assert response.usage.total_tokens == 1000 + 50

    @pytest.mark.asyncio
    async def test_stream_extracts_cached_tokens(self) -> None:
        """Streaming should extract cached tokens from usage-only chunk."""
        client = OpenAIClient(api_key="test-key")
        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        # Create a usage-only chunk (no choices, has usage)
        usage_chunk = MagicMock()
        usage_chunk.choices = []
        usage_chunk.usage = MagicMock()
        usage_chunk.usage.prompt_tokens = 1000
        usage_chunk.usage.completion_tokens = 50
        usage_chunk.usage.prompt_tokens_details = MagicMock()
        usage_chunk.usage.prompt_tokens_details.cached_tokens = 700

        class SingleChunkIter:
            def __init__(self) -> None:
                self._yielded = False

            def __aiter__(self) -> "SingleChunkIter":
                return self

            async def __anext__(self) -> MagicMock:
                if self._yielded:
                    raise StopAsyncIteration
                self._yielded = True
                return usage_chunk

        mock_create.return_value = SingleChunkIter()

        chunks = []
        async for chunk in client.stream(
            messages=[Message(role=Role.USER, content="Hi")],
            model=Model.GPT_5_NANO,
        ):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].usage is not None
        assert chunks[0].usage.input_tokens == 300
        assert chunks[0].usage.cache_read_tokens == 700
        assert chunks[0].usage.output_tokens == 50

    @pytest.mark.asyncio
    async def test_stream_no_cache_details(self) -> None:
        """Streaming should handle missing prompt_tokens_details gracefully."""
        client = OpenAIClient(api_key="test-key")
        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        usage_chunk = MagicMock()
        usage_chunk.choices = []
        usage_chunk.usage = MagicMock()
        usage_chunk.usage.prompt_tokens = 500
        usage_chunk.usage.completion_tokens = 20
        usage_chunk.usage.prompt_tokens_details = None

        class SingleChunkIter:
            def __init__(self) -> None:
                self._yielded = False

            def __aiter__(self) -> "SingleChunkIter":
                return self

            async def __anext__(self) -> MagicMock:
                if self._yielded:
                    raise StopAsyncIteration
                self._yielded = True
                return usage_chunk

        mock_create.return_value = SingleChunkIter()

        chunks = []
        async for chunk in client.stream(
            messages=[Message(role=Role.USER, content="Hi")],
            model=Model.GPT_5_NANO,
        ):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].usage is not None
        assert chunks[0].usage.input_tokens == 500
        assert chunks[0].usage.cache_read_tokens == 0

    @pytest.mark.asyncio
    async def test_stream_chunks_carry_api_model(self) -> None:
        """Every chunk carries the API-reported model, usage-only included."""
        client = OpenAIClient(api_key="test-key")
        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        content_chunk = MagicMock()
        content_chunk.model = "gpt-5-nano-2026-01-01"
        content_chunk.choices = [MagicMock()]
        content_chunk.choices[0].delta.content = "Hi"
        content_chunk.choices[0].delta.tool_calls = None
        content_chunk.choices[0].finish_reason = "stop"
        content_chunk.usage = None

        usage_chunk = MagicMock()
        usage_chunk.model = "gpt-5-nano-2026-01-01"
        usage_chunk.choices = []
        usage_chunk.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
        usage_chunk.usage.prompt_tokens_details = None

        class TwoChunkIter:
            def __init__(self) -> None:
                self._items = [content_chunk, usage_chunk]
                self._index = 0

            def __aiter__(self) -> "TwoChunkIter":
                return self

            async def __anext__(self) -> MagicMock:
                if self._index >= len(self._items):
                    raise StopAsyncIteration
                item = self._items[self._index]
                self._index += 1
                return item

        mock_create.return_value = TwoChunkIter()

        chunks = []
        async for chunk in client.stream(
            messages=[Message(role=Role.USER, content="Hi")],
            model=Model.GPT_5_NANO,
        ):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert all(c.model == "gpt-5-nano-2026-01-01" for c in chunks)


@pytest.mark.unit
class TestOpenAIMultimodalRejected:
    """OpenAI's converter is text-only — block content must fail loudly."""

    def test_convert_messages_block_content_raises(self) -> None:
        from neosian._foundation.llm.base import DocumentBlock, TextBlock
        from neosian._foundation.shared.exceptions import UnsupportedContentError

        client = OpenAIClient(api_key="test-key")
        messages = [
            Message(
                role=Role.USER,
                content=[
                    DocumentBlock(media_type="application/pdf", data="JVBERi0="),
                    TextBlock(text="Transcribe this."),
                ],
            ),
        ]

        with pytest.raises(UnsupportedContentError, match="openai"):
            client._convert_messages(messages)


@pytest.mark.unit
class TestOpenAINestedSchema:
    """OpenAI strict mode requires additionalProperties on every object."""

    def test_nested_model_defs_carry_additional_properties(self) -> None:
        from pydantic import BaseModel

        from neosian._foundation.shared.types import ResponseFormat

        class Question(BaseModel):
            question: str
            options: list[str]

        class Quiz(BaseModel):
            questions: list[Question]

        client = OpenAIClient(api_key="test-key")
        payload = client._convert_response_format(ResponseFormat(schema=Quiz))
        schema = cast(Any, payload)["json_schema"]["schema"]

        assert schema["additionalProperties"] is False
        assert schema["$defs"]["Question"]["additionalProperties"] is False


@pytest.mark.unit
class TestNativeTypeIgnored:
    def test_marked_definition_keeps_the_function_schema(self) -> None:
        """native_type degrades by construction — the reason fallback off
        Anthropic needs no capability gate (ledger #44)."""
        client = OpenAIClient(api_key="test-api-key")
        tool = ToolDefinition(
            name=ToolName("memory"),
            description="The memory tool",
            parameters={"type": "object", "properties": {}},
            native_type="memory_20250818",
        )
        converted = client._convert_tools([tool])
        assert converted == [
            {
                "type": "function",
                "function": {
                    "name": "memory",
                    "description": "The memory tool",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]


@pytest.mark.unit
class TestCompactionRejected:
    def test_compaction_bearing_message_raises(self) -> None:
        """A server-compaction history cannot cross to this provider —
        the fallback gate turns this guaranteed failure into a skip."""
        client = OpenAIClient(api_key="test-api-key")
        messages = [
            Message(
                role=Role.ASSISTANT,
                content=[CompactionBlock(content="summary")],
            )
        ]
        with pytest.raises(UnsupportedContentError):
            client._convert_messages(messages)
