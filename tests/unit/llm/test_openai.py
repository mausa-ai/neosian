"""Tests for OpenAI LLM client."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from openai import BadRequestError

from neosian._foundation.llm.base import Message, Role, ToolDefinition
from neosian._foundation.llm.openai import OpenAIClient
from neosian._foundation.shared.constants import LLMDefaults
from neosian._foundation.shared.exceptions import (
    ToolCallGenerationError,
    UnsupportedParameterError,
)
from neosian._foundation.shared.types import ModelId, ToolName


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
        client._client.chat.completions.create = mock_create

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
            model=ModelId("gpt-4o-mini"),
            tools=tools,
        )

        assert result.message.content == "Success"
        assert mock_create.call_count == 2

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self) -> None:
        """Should raise ToolCallGenerationError after max retries."""
        client = OpenAIClient(api_key="test-key")

        mock_create = AsyncMock()
        client._client.chat.completions.create = mock_create

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
                model=ModelId("gpt-4o-mini"),
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
        client._client.chat.completions.create = mock_create

        tool_error = BadRequestError(
            message="Invalid tool call",
            body={"error": {"code": "invalid_tool_call", "message": "Failed"}},
            response=MagicMock(),
        )

        mock_create.side_effect = tool_error

        # No tools provided - should re-raise immediately
        with pytest.raises(BadRequestError):
            await client.complete(
                messages=[Message(role=Role.USER, content="Hi")],
                model=ModelId("gpt-4o-mini"),
                tools=None,
            )

        assert mock_create.call_count == 1

    @pytest.mark.asyncio
    async def test_non_tool_error_reraises_immediately(self) -> None:
        """Should re-raise non-tool BadRequestError without retry."""
        client = OpenAIClient(api_key="test-key")

        mock_create = AsyncMock()
        client._client.chat.completions.create = mock_create

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
        with pytest.raises(BadRequestError):
            await client.complete(
                messages=[Message(role=Role.USER, content="Hi")],
                model=ModelId("gpt-4o-mini"),
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
                model=ModelId("gpt-5-nano"),
                temperature=0.5,
            )

        assert "temperature" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_no_temperature_works(self) -> None:
        """Should work when temperature is not provided."""
        client = OpenAIClient(api_key="test-key")

        mock_create = AsyncMock()
        client._client.chat.completions.create = mock_create

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
            model=ModelId("gpt-5-nano"),
        )

        assert result.message.content == "Hello"
        mock_create.assert_called_once()
