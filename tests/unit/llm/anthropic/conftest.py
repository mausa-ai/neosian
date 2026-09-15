"""Fixtures for the Anthropic client suites."""

import dataclasses

import pytest

from neosian._foundation.llm.anthropic import AnthropicClient
from neosian._foundation.llm.base import (
    Message,
    Role,
    ToolDefinition,
)
from neosian._foundation.shared import models as models_module
from neosian._foundation.shared.types import Model, ToolName


@pytest.fixture
def client() -> AnthropicClient:
    """Create a client instance with a test API key."""
    return AnthropicClient(api_key="test-api-key")


@pytest.fixture
def plain_sonnet(monkeypatch: pytest.MonkeyPatch) -> Model:
    """Sonnet 5 outside the reasoning and compaction sets: no shipped
    Anthropic row is, and the gates stay the row's."""
    spec = models_module._MODEL_SPECS[Model.CLAUDE_SONNET_5.value]
    plain = dataclasses.replace(
        spec, supports_reasoning=False, supports_compaction_blocks=False
    )
    monkeypatch.setitem(models_module._MODEL_SPECS, Model.CLAUDE_SONNET_5.value, plain)
    return Model.CLAUDE_SONNET_5


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
