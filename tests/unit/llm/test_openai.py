"""Tests for OpenAI LLM client."""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from openai import BadRequestError
from openai.types.chat.chat_completion_chunk import (
    ChoiceDeltaToolCall,
    ChoiceDeltaToolCallFunction,
)

from neosian._foundation.llm.base import (
    CompactionBlock,
    Message,
    Role,
    StopReason,
    ToolDefinition,
    Usage,
    normalize_stop_reason,
)
from neosian._foundation.llm.openai import OpenAIClient
from neosian._foundation.llm.openai_convert import convert_tool_choice
from neosian._foundation.shared.constants import LLMDefaults
from neosian._foundation.shared.exceptions import (
    ProviderError,
    ToolCallGenerationError,
    UnsupportedContentError,
    UnsupportedParameterError,
)
from neosian._foundation.shared.types import (
    Model,
    ReasoningEffort,
    ToolChoice,
    ToolName,
)
from tests.unit.llm.sdk_specs import OPENAI as SPEC, autospec


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
        assert "strict" not in result[0]["function"]

    def test_a_strict_tool_is_sent_strict(self) -> None:
        """`strict=True` reaches the wire in the strict-mode shape (TG-9)."""
        client = OpenAIClient(api_key="test-key")
        tool = ToolDefinition(
            name=ToolName("search"),
            description="Search",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {
                        "anyOf": [{"type": "integer"}, {"type": "null"}],
                        "default": None,
                    },
                },
                "required": ["query"],
            },
            strict=True,
        )

        function = client._convert_tools([tool])[0]["function"]

        assert function["strict"] is True
        parameters = cast(dict[str, Any], function["parameters"])
        assert parameters["required"] == ["query", "limit"]
        assert "default" not in parameters["properties"]["limit"]
        assert parameters["additionalProperties"] is False
        assert tool.parameters["required"] == ["query"]  # untouched


@pytest.mark.unit
class TestOpenAIArgumentFragments:
    """Argument deltas reach the caller as well as the builder (#226)."""

    def _chunk(self, arguments: str | None, *, call_id: str | None, finish: str | None):  # type: ignore[no-untyped-def]
        chunk = autospec(SPEC["chunk"])
        chunk.choices = [autospec(SPEC["chunk_choice"])]
        chunk.choices[0].delta.content = None
        chunk.choices[0].delta.tool_calls = (
            [
                ChoiceDeltaToolCall(
                    index=0,
                    id=call_id,
                    type="function",
                    function=ChoiceDeltaToolCallFunction(
                        name="get_weather" if call_id else None, arguments=arguments
                    ),
                )
            ]
            if arguments is not None
            else None
        )
        chunk.choices[0].finish_reason = finish
        chunk.usage = None
        return chunk

    @pytest.mark.asyncio
    async def test_each_delta_is_forwarded_and_still_assembled(self) -> None:
        client = OpenAIClient(api_key="test-key")
        first = self._chunk('{"location":', call_id="call_1", finish=None)
        # The wire announces the id once; a later fragment still carries it.
        second = self._chunk(' "Paris"}', call_id=None, finish=None)
        terminal = self._chunk(None, call_id=None, finish="tool_calls")

        async def chunks() -> Any:
            yield first
            yield second
            yield terminal

        _sdk(client).chat.completions.create = AsyncMock(return_value=chunks())

        streamed = [
            chunk
            async for chunk in client.stream(
                messages=[Message(role=Role.USER, content="Hi")],
                model=Model.GPT_5_6_LUNA,
            )
        ]

        fragments = [f for chunk in streamed for f in chunk.tool_call_fragments]
        assert [f.fragment for f in fragments] == ['{"location":', ' "Paris"}']
        assert {f.id for f in fragments} == {"call_1"}
        assert {f.name for f in fragments} == {"get_weather"}
        assert streamed[-1].tool_calls[0].arguments == {"location": "Paris"}

    @pytest.mark.asyncio
    async def test_a_content_chunk_carries_no_fragments(self) -> None:
        client = OpenAIClient(api_key="test-key")
        chunk = autospec(SPEC["chunk"])
        chunk.choices = [autospec(SPEC["chunk_choice"])]
        chunk.choices[0].delta.content = "hi"
        chunk.choices[0].delta.tool_calls = None
        chunk.choices[0].finish_reason = "stop"
        chunk.usage = None

        async def chunks() -> Any:
            yield chunk

        _sdk(client).chat.completions.create = AsyncMock(return_value=chunks())

        streamed = [
            piece
            async for piece in client.stream(
                messages=[Message(role=Role.USER, content="Hi")],
                model=Model.GPT_5_6_LUNA,
            )
        ]
        assert all(piece.tool_call_fragments == () for piece in streamed)


@pytest.mark.unit
class TestOpenAIToolChoice:
    """Three modes are the wire's own strings, a named tool its object
    form, and `parallel` rides the body flag beside them (NC9 #224)."""

    def _mock_response(self) -> Any:
        mock = autospec(SPEC["completion"])
        mock.choices = [autospec(SPEC["choice"])]
        mock.choices[0].message.content = "Response"
        mock.choices[0].message.tool_calls = None
        mock.usage.prompt_tokens = 10
        mock.usage.completion_tokens = 5
        mock.model = "gpt-5.6-luna"
        return mock

    def _tool(self) -> ToolDefinition:
        return ToolDefinition(
            name=ToolName("search"),
            description="Search",
            parameters={"type": "object", "properties": {}},
        )

    @pytest.mark.parametrize(
        ("choice", "expected"),
        [
            (ToolChoice.auto(), "auto"),
            (ToolChoice.required(), "required"),
            (ToolChoice.none(), "none"),
            (
                ToolChoice.tool("search"),
                {"type": "function", "function": {"name": "search"}},
            ),
        ],
        ids=["auto", "required", "none", "tool"],
    )
    def test_the_wire_shapes(self, choice: ToolChoice, expected: object) -> None:
        assert convert_tool_choice(choice) == expected

    @pytest.mark.asyncio
    async def test_the_choice_reaches_the_body_beside_the_tools(self) -> None:
        client = OpenAIClient(api_key="test-key")
        mock_create = AsyncMock(return_value=self._mock_response())
        _sdk(client).chat.completions.create = mock_create

        await client.complete(
            messages=[Message(role=Role.USER, content="Hi")],
            model=Model.GPT_5_6_LUNA,
            tools=[self._tool()],
            tool_choice=ToolChoice.required(parallel=False),
        )

        kwargs = mock_create.call_args.kwargs
        assert kwargs["tool_choice"] == "required"
        assert kwargs["parallel_tool_calls"] is False

    @pytest.mark.asyncio
    async def test_the_parallel_default_sends_no_flag(self) -> None:
        """Only a False says something the wire's default does not."""
        client = OpenAIClient(api_key="test-key")
        mock_create = AsyncMock(return_value=self._mock_response())
        _sdk(client).chat.completions.create = mock_create

        await client.complete(
            messages=[Message(role=Role.USER, content="Hi")],
            model=Model.GPT_5_6_LUNA,
            tools=[self._tool()],
            tool_choice=ToolChoice.auto(),
        )

        assert "parallel_tool_calls" not in mock_create.call_args.kwargs

    @pytest.mark.asyncio
    async def test_without_tools_no_choice_is_sent(self) -> None:
        client = OpenAIClient(api_key="test-key")
        mock_create = AsyncMock(return_value=self._mock_response())
        _sdk(client).chat.completions.create = mock_create

        await client.complete(
            messages=[Message(role=Role.USER, content="Hi")],
            model=Model.GPT_5_6_LUNA,
            tool_choice=ToolChoice.required(),
        )

        assert "tool_choice" not in mock_create.call_args.kwargs


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

        mock_response = autospec(SPEC["completion"])
        mock_response.choices = [autospec(SPEC["choice"])]
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
            model=Model.GPT_5_6_TERRA,
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
                model=Model.GPT_5_6_TERRA,
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
                model=Model.GPT_5_6_TERRA,
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
                model=Model.GPT_5_6_TERRA,
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
                model=Model.GPT_5_6_LUNA,
                temperature=0.5,
            )

        assert "temperature" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_no_temperature_works(self) -> None:
        """Should work when temperature is not provided."""
        client = OpenAIClient(api_key="test-key")

        mock_create = AsyncMock()
        _sdk(client).chat.completions.create = mock_create

        mock_response = autospec(SPEC["completion"])
        mock_response.choices = [autospec(SPEC["choice"])]
        mock_response.choices[0].message.content = "Hello"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.model = "gpt-5.6-luna"

        mock_create.return_value = mock_response

        result = await client.complete(
            messages=[Message(role=Role.USER, content="Hi")],
            model=Model.GPT_5_6_LUNA,
        )

        assert result.message.content == "Hello"
        mock_create.assert_called_once()


@pytest.mark.unit
class TestOpenAIClientReasoningEffort:
    """Test reasoning_effort parameter handling for OpenAI GPT-5 models."""

    def _mock_response(self, model: str = "gpt-5.6-luna") -> Any:
        """Create a mock OpenAI response."""
        mock = autospec(SPEC["completion"])
        mock.choices = [autospec(SPEC["choice"])]
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
            model=Model.GPT_5_6_LUNA,
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
            model=Model.GPT_5_6_LUNA,
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
            model=Model.GPT_5_6_LUNA,
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
            model=Model.GPT_5_1,
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
                model=Model.GPT_5_1,
                reasoning_effort=ReasoningEffort.MAX,
            )

        assert any("MAX" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_max_passes_through_where_the_spec_allows(self) -> None:
        """A row with supports_max_effort sends `max` as is (§31)."""
        client = OpenAIClient(api_key="test-key")
        mock_create = AsyncMock(return_value=self._mock_response("gpt-5.6-sol"))
        _sdk(client).chat.completions.create = mock_create

        await client.complete(
            messages=[Message(role=Role.USER, content="Think")],
            model=Model.GPT_5_6_SOL,
            reasoning_effort=ReasoningEffort.MAX,
        )

        assert mock_create.call_args.kwargs["reasoning_effort"] == "max"

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
            model=Model.GPT_5_6_LUNA,
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
            model=Model.GPT_5_1,
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
        model: str = "gpt-5.6-luna",
    ) -> Any:
        """Create a mock OpenAI response with optional cache details."""
        mock = autospec(SPEC["completion"])
        mock.choices = [autospec(SPEC["choice"])]
        mock.choices[0].message.content = "Hello"
        mock.choices[0].message.tool_calls = None
        mock.usage.prompt_tokens = prompt_tokens
        mock.usage.completion_tokens = completion_tokens

        if cached_tokens is not None:
            mock.usage.prompt_tokens_details = MagicMock(spec_set=SPEC["details"])
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
            model=Model.GPT_5_6_LUNA,
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
            model=Model.GPT_5_6_LUNA,
        )

        assert response.usage.input_tokens == 500
        assert response.usage.cache_read_tokens == 0

    @pytest.mark.asyncio
    async def test_parse_response_cached_tokens_none(self) -> None:
        """Should handle cached_tokens=None in prompt_tokens_details."""
        client = OpenAIClient(api_key="test-key")
        mock_resp = self._mock_response(prompt_tokens=500, completion_tokens=20)
        mock_resp.usage.prompt_tokens_details = MagicMock(spec_set=SPEC["details"])
        mock_resp.usage.prompt_tokens_details.cached_tokens = None
        mock_create = AsyncMock(return_value=mock_resp)
        _sdk(client).chat.completions.create = mock_create

        response = await client.complete(
            messages=[Message(role=Role.USER, content="Hi")],
            model=Model.GPT_5_6_LUNA,
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
            model=Model.GPT_5_6_LUNA,
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
        usage_chunk = autospec(SPEC["chunk"])
        usage_chunk.choices = []
        usage_chunk.usage = autospec(SPEC["usage"])
        usage_chunk.usage.prompt_tokens = 1000
        usage_chunk.usage.completion_tokens = 50
        usage_chunk.usage.prompt_tokens_details = MagicMock(spec_set=SPEC["details"])
        usage_chunk.usage.prompt_tokens_details.cached_tokens = 700

        class SingleChunkIter:
            def __init__(self) -> None:
                self._yielded = False

            def __aiter__(self) -> "SingleChunkIter":
                return self

            async def __anext__(self) -> Any:
                if self._yielded:
                    raise StopAsyncIteration
                self._yielded = True
                return usage_chunk

        mock_create.return_value = SingleChunkIter()

        chunks = []
        async for chunk in client.stream(
            messages=[Message(role=Role.USER, content="Hi")],
            model=Model.GPT_5_6_LUNA,
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

        usage_chunk = autospec(SPEC["chunk"])
        usage_chunk.choices = []
        usage_chunk.usage = autospec(SPEC["usage"])
        usage_chunk.usage.prompt_tokens = 500
        usage_chunk.usage.completion_tokens = 20
        usage_chunk.usage.prompt_tokens_details = None

        class SingleChunkIter:
            def __init__(self) -> None:
                self._yielded = False

            def __aiter__(self) -> "SingleChunkIter":
                return self

            async def __anext__(self) -> Any:
                if self._yielded:
                    raise StopAsyncIteration
                self._yielded = True
                return usage_chunk

        mock_create.return_value = SingleChunkIter()

        chunks = []
        async for chunk in client.stream(
            messages=[Message(role=Role.USER, content="Hi")],
            model=Model.GPT_5_6_LUNA,
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

        content_chunk = autospec(SPEC["chunk"])
        content_chunk.model = "gpt-5.6-luna-2026-01-01"
        content_chunk.choices = [autospec(SPEC["chunk_choice"])]
        content_chunk.choices[0].delta.content = "Hi"
        content_chunk.choices[0].delta.tool_calls = None
        content_chunk.choices[0].finish_reason = "stop"
        content_chunk.usage = None

        usage_chunk = autospec(SPEC["chunk"])
        usage_chunk.model = "gpt-5.6-luna-2026-01-01"
        usage_chunk.choices = []
        usage_chunk.usage = MagicMock(
            spec=SPEC["usage"], prompt_tokens=10, completion_tokens=5
        )
        usage_chunk.usage.prompt_tokens_details = None

        class TwoChunkIter:
            def __init__(self) -> None:
                self._items = [content_chunk, usage_chunk]
                self._index = 0

            def __aiter__(self) -> "TwoChunkIter":
                return self

            async def __anext__(self) -> Any:
                if self._index >= len(self._items):
                    raise StopAsyncIteration
                item = self._items[self._index]
                self._index += 1
                return item

        mock_create.return_value = TwoChunkIter()

        chunks = []
        async for chunk in client.stream(
            messages=[Message(role=Role.USER, content="Hi")],
            model=Model.GPT_5_6_LUNA,
        ):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert all(c.model == "gpt-5.6-luna-2026-01-01" for c in chunks)

    async def test_a_refusal_is_the_content_and_the_stop_reason(self) -> None:
        """OpenAI's `refusal` field reads into content_filter (LL-14)."""
        client = OpenAIClient(api_key="test-key")
        mock_response = autospec(SPEC["completion"])
        mock_response.choices = [autospec(SPEC["choice"])]
        mock_response.choices[0].message.content = None
        mock_response.choices[0].message.refusal = "I can't help with that."
        mock_response.choices[0].message.tool_calls = None
        mock_response.choices[0].finish_reason = "stop"
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.model = "gpt-5.6-luna"
        _sdk(client).chat.completions.create = AsyncMock(return_value=mock_response)

        result = await client.complete(
            messages=[Message(role=Role.USER, content="Hi")], model=Model.GPT_5_6_LUNA
        )
        assert result.message.content == "I can't help with that."
        assert result.stop_reason == "refusal"
        assert normalize_stop_reason(result.stop_reason) is StopReason.CONTENT_FILTER

    async def test_a_streamed_refusal_names_the_terminal_chunk(self) -> None:
        """Refusal deltas are content; the chunk that ends the turn says
        `refusal`, whatever the wire's own finish reason (LL-14)."""
        client = OpenAIClient(api_key="test-key")
        first = autospec(SPEC["chunk"])
        first.choices = [autospec(SPEC["chunk_choice"])]
        first.choices[0].delta.content = None
        first.choices[0].delta.refusal = "I can't"
        first.choices[0].delta.tool_calls = None
        first.choices[0].finish_reason = None
        first.usage = None
        last = autospec(SPEC["chunk"])
        last.choices = [autospec(SPEC["chunk_choice"])]
        last.choices[0].delta.content = None
        last.choices[0].delta.refusal = None
        last.choices[0].delta.tool_calls = None
        last.choices[0].finish_reason = "stop"
        last.usage = None

        async def chunks() -> Any:
            yield first
            yield last

        _sdk(client).chat.completions.create = AsyncMock(return_value=chunks())
        received = []
        async for item in client.stream(
            messages=[Message(role=Role.USER, content="Hi")], model=Model.GPT_5_6_LUNA
        ):
            received.append(item)
        assert received[0].content == "I can't"
        assert received[0].finish_reason is None
        assert received[-1].finish_reason == "refusal"

    async def test_usage_on_a_content_chunk_is_read(self) -> None:
        """A door that attaches usage to its final content chunk is not
        billed at zero (LL-12)."""
        client = OpenAIClient(api_key="test-key")
        chunk = autospec(SPEC["chunk"])
        chunk.choices = [autospec(SPEC["chunk_choice"])]
        chunk.choices[0].delta.content = "Hi"
        chunk.choices[0].delta.tool_calls = None
        chunk.choices[0].finish_reason = "stop"
        chunk.usage = MagicMock(
            spec=SPEC["usage"], prompt_tokens=10, completion_tokens=5
        )
        chunk.usage.prompt_tokens_details = None

        async def chunks() -> Any:
            yield chunk

        _sdk(client).chat.completions.create = AsyncMock(return_value=chunks())

        received = []
        async for item in client.stream(
            messages=[Message(role=Role.USER, content="Hi")], model=Model.GPT_5_6_LUNA
        ):
            received.append(item)
        assert len(received) == 1
        assert received[0].content == "Hi"
        assert received[0].usage == Usage(input_tokens=10, output_tokens=5)

    async def test_a_truncated_tool_call_names_the_stop_reason(self) -> None:
        """Arguments cut off at `length` raise naming that reason (LL-7)."""
        client = OpenAIClient(api_key="test-key")

        partial = autospec(SPEC["chunk"])
        partial.choices = [autospec(SPEC["chunk_choice"])]
        partial.choices[0].delta.content = None
        partial.choices[0].delta.tool_calls = [
            ChoiceDeltaToolCall(
                index=0,
                id="call_1",
                type="function",
                function=ChoiceDeltaToolCallFunction(
                    name="get_weather", arguments='{"location":'
                ),
            )
        ]
        partial.choices[0].finish_reason = None
        partial.usage = None
        terminal = autospec(SPEC["chunk"])
        terminal.choices = [autospec(SPEC["chunk_choice"])]
        terminal.choices[0].delta.content = None
        terminal.choices[0].delta.tool_calls = None
        terminal.choices[0].finish_reason = "length"
        terminal.usage = None

        async def chunks() -> Any:
            yield partial
            yield terminal

        _sdk(client).chat.completions.create = AsyncMock(return_value=chunks())

        with pytest.raises(ProviderError, match="stop reason: length") as info:
            async for _ in client.stream(
                messages=[Message(role=Role.USER, content="Hi")],
                model=Model.GPT_5_6_LUNA,
            ):
                pass
        assert info.value.provider == "openai"


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
