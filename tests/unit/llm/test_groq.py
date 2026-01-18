"""Tests for Groq LLM client."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from groq import BadRequestError

from neosian._foundation.llm.base import Message, Role, ToolDefinition
from neosian._foundation.llm.groq import GroqClient
from neosian._foundation.shared.constants import LLMDefaults
from neosian._foundation.shared.exceptions import ToolCallGenerationError
from neosian._foundation.shared.types import Model, ToolName


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
        client._client.chat.completions.create = mock_create

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
            model=Model.GPT_OSS_20B,
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
        client._client.chat.completions.create = mock_create

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
                model=Model.GPT_OSS_20B,
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
        client._client.chat.completions.create = mock_create

        tool_error = BadRequestError(
            message="Tool use failed",
            body={"error": {"code": "tool_use_failed", "message": "Failed"}},
            response=MagicMock(),
        )

        mock_create.side_effect = tool_error

        # No tools provided - should re-raise immediately
        with pytest.raises(BadRequestError):
            await client.complete(
                messages=[Message(role=Role.USER, content="Hi")],
                model=Model.GPT_OSS_20B,
                tools=None,
            )

        assert mock_create.call_count == 1

    @pytest.mark.asyncio
    async def test_custom_temperature_used(self) -> None:
        """Should use custom temperature when provided."""
        client = GroqClient(api_key="test-key")

        mock_create = AsyncMock()
        client._client.chat.completions.create = mock_create

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
            model=Model.GPT_OSS_20B,
            temperature=0.5,
        )

        mock_create.assert_called_once()
        assert mock_create.call_args.kwargs["temperature"] == 0.5

    @pytest.mark.asyncio
    async def test_non_tool_error_reraises_immediately(self) -> None:
        """Should re-raise non-tool BadRequestError without retry."""
        client = GroqClient(api_key="test-key")

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
                model=Model.GPT_OSS_20B,
                tools=tools,
            )

        # Only one attempt - no retries for non-tool errors
        assert mock_create.call_count == 1
