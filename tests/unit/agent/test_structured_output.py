"""Tests for structured output support."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from neosian import (
    Agent,
    AgentConfig,
    Message,
    ResponseFormat,
    Role,
    StructuredOutputStreamingError,
    StructuredOutputToolsError,
    Tool,
    ToolResult,
)
from neosian._foundation.llm.base import CompletionResponse, Usage


class WeatherResponse(BaseModel):
    """Test Pydantic model for structured output."""

    temperature: float
    conditions: str
    humidity: int


class SimpleResponse(BaseModel):
    """Simple test model."""

    message: str


@pytest.mark.unit
class TestResponseFormatType:
    """Tests for ResponseFormat dataclass."""

    def test_response_format_with_schema(self) -> None:
        """Test creating ResponseFormat with a Pydantic schema."""
        rf = ResponseFormat(schema=WeatherResponse)
        assert rf.schema == WeatherResponse
        assert rf.strict is True  # Default

    def test_response_format_strict_false(self) -> None:
        """Test creating ResponseFormat with strict=False."""
        rf = ResponseFormat(schema=WeatherResponse, strict=False)
        assert rf.schema == WeatherResponse
        assert rf.strict is False


@pytest.mark.unit
class TestStructuredOutputValidation:
    """Tests for structured output validation errors."""

    @pytest.fixture
    def mock_router(self) -> MagicMock:
        """Create a mock router."""
        router = MagicMock()
        return router

    @pytest.fixture
    def agent_without_tools(self, mock_router: MagicMock) -> Agent:
        """Create an agent without tools (including todo disabled)."""
        config = AgentConfig(
            system_prompt="You are a helpful assistant.",
            enable_todo=False,  # Disable todo to have no tools
        )
        agent = Agent(config=config)
        agent._router = mock_router
        return agent

    @pytest.fixture
    def agent_with_tools(self, mock_router: MagicMock) -> Agent:
        """Create an agent with tools."""

        @Tool(name="get_time", description="Get the current time")
        async def get_time() -> ToolResult[str]:
            return ToolResult(success=True, data="12:00 PM")

        config = AgentConfig(
            system_prompt="You are a helpful assistant.",
            tools=[get_time],
            enable_todo=False,
        )
        agent = Agent(config=config)
        agent._router = mock_router
        return agent

    @pytest.mark.asyncio
    async def test_streaming_with_response_format_raises_error(
        self, agent_without_tools: Agent
    ) -> None:
        """Test that streaming + response_format raises StructuredOutputStreamingError."""
        messages = [Message(role=Role.USER, content="What's the weather?")]
        rf = ResponseFormat(schema=WeatherResponse)

        with pytest.raises(StructuredOutputStreamingError):
            await agent_without_tools.run(
                messages, stream=True, response_format=rf  # type: ignore[arg-type]
            )

    @pytest.mark.asyncio
    async def test_tools_with_response_format_raises_error(
        self, agent_with_tools: Agent
    ) -> None:
        """Test that tools + response_format raises StructuredOutputToolsError."""
        messages = [Message(role=Role.USER, content="What time is it?")]
        rf = ResponseFormat(schema=WeatherResponse)

        with pytest.raises(StructuredOutputToolsError):
            await agent_with_tools.run(messages, stream=False, response_format=rf)

    @pytest.mark.asyncio
    async def test_response_format_without_streaming_allowed(
        self, agent_without_tools: Agent, mock_router: MagicMock
    ) -> None:
        """Test that response_format works without streaming."""
        # Setup mock client
        mock_client = AsyncMock()
        mock_client.complete = AsyncMock(
            return_value=CompletionResponse(
                message=Message(
                    role=Role.ASSISTANT,
                    content='{"temperature": 72.5, "conditions": "sunny", "humidity": 45}',
                ),
                usage=Usage(input_tokens=10, output_tokens=20),
                model="gpt-5-nano",
            )
        )
        mock_router.create_client.return_value = mock_client

        messages = [Message(role=Role.USER, content="What's the weather?")]
        rf = ResponseFormat(schema=WeatherResponse)

        response = await agent_without_tools.run(
            messages, stream=False, response_format=rf
        )

        # Verify response_format was passed to client
        mock_client.complete.assert_called_once()
        call_args = mock_client.complete.call_args
        assert call_args.kwargs["response_format"] == rf

    @pytest.mark.asyncio
    async def test_response_parsed_into_pydantic_model(
        self, agent_without_tools: Agent, mock_router: MagicMock
    ) -> None:
        """Test that response is parsed into Pydantic model."""
        # Setup mock client
        mock_client = AsyncMock()
        mock_client.complete = AsyncMock(
            return_value=CompletionResponse(
                message=Message(
                    role=Role.ASSISTANT,
                    content='{"temperature": 72.5, "conditions": "sunny", "humidity": 45}',
                ),
                usage=Usage(input_tokens=10, output_tokens=20),
                model="gpt-5-nano",
            )
        )
        mock_router.create_client.return_value = mock_client

        messages = [Message(role=Role.USER, content="What's the weather?")]
        rf = ResponseFormat(schema=WeatherResponse)

        response = await agent_without_tools.run(
            messages, stream=False, response_format=rf
        )

        # Verify parsed field is populated
        assert response.parsed is not None
        assert isinstance(response.parsed, WeatherResponse)
        assert response.parsed.temperature == 72.5
        assert response.parsed.conditions == "sunny"
        assert response.parsed.humidity == 45

    @pytest.mark.asyncio
    async def test_response_without_format_has_no_parsed(
        self, agent_without_tools: Agent, mock_router: MagicMock
    ) -> None:
        """Test that response without response_format has parsed=None."""
        # Setup mock client
        mock_client = AsyncMock()
        mock_client.complete = AsyncMock(
            return_value=CompletionResponse(
                message=Message(
                    role=Role.ASSISTANT,
                    content="The weather is sunny.",
                ),
                usage=Usage(input_tokens=10, output_tokens=20),
                model="gpt-5-nano",
            )
        )
        mock_router.create_client.return_value = mock_client

        messages = [Message(role=Role.USER, content="What's the weather?")]

        response = await agent_without_tools.run(messages, stream=False)

        # Verify parsed field is None
        assert response.parsed is None


@pytest.mark.unit
class TestResponseFormatJsonSchema:
    """Tests for JSON schema generation from Pydantic models."""

    def test_pydantic_model_json_schema(self) -> None:
        """Test that Pydantic model generates valid JSON schema."""
        schema = WeatherResponse.model_json_schema()

        assert schema["type"] == "object"
        assert "properties" in schema
        assert "temperature" in schema["properties"]
        assert "conditions" in schema["properties"]
        assert "humidity" in schema["properties"]
        assert schema["properties"]["temperature"]["type"] == "number"
        assert schema["properties"]["conditions"]["type"] == "string"
        assert schema["properties"]["humidity"]["type"] == "integer"
