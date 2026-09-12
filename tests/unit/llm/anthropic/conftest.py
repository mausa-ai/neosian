"""Fixtures for the Anthropic client suites."""

import pytest

from neosian._foundation.llm.anthropic import AnthropicClient
from neosian._foundation.llm.base import (
    Message,
    Role,
    ToolDefinition,
)
from neosian._foundation.shared.types import ToolName


@pytest.fixture
def client() -> AnthropicClient:
    """Create a client instance with a test API key."""
    return AnthropicClient(api_key="test-api-key")


@pytest.fixture
def sample_messages() -> list[Message]:
    """Sample conversation messages."""
    return [
        Message(role=Role.SYSTEM, content="You are a helpful assistant."),
        Message(role=Role.USER, content="Hello!"),
    ]


@pytest.fixture
def sample_tool() -> ToolDefinition:
    """Sample tool definition."""
    return ToolDefinition(
        name=ToolName("get_weather"),
        description="Get the weather for a location",
        parameters={
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "The city name"},
            },
            "required": ["location"],
        },
    )
