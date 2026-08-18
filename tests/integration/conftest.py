"""Fixtures for integration tests.

Integration tests require real API keys set as environment variables:
- GROQ_API_KEY: Required for Groq LLM tests
- CEREBRAS_API_KEY: Required for Cerebras LLM tests
- ANTHROPIC_API_KEY: Required for Anthropic LLM tests (multimodal)

Run with: GROQ_API_KEY=gsk_xxx uv run pytest -m integration -v
"""

import os

import pytest

from neosian._foundation.llm.anthropic import AnthropicClient
from neosian._foundation.llm.cerebras import CerebrasClient
from neosian._foundation.llm.groq import GroqClient


def get_groq_api_key() -> str | None:
    """Get Groq API key from environment."""
    return os.environ.get("GROQ_API_KEY")


def get_cerebras_api_key() -> str | None:
    """Get Cerebras API key from environment."""
    return os.environ.get("CEREBRAS_API_KEY")


@pytest.fixture
def groq_api_key() -> str:
    """Fixture that provides Groq API key or skips test."""
    key = get_groq_api_key()
    if not key:
        pytest.skip("GROQ_API_KEY environment variable not set")
    return key


@pytest.fixture
def groq_client(groq_api_key: str) -> GroqClient:
    """Fixture that provides a configured Groq client."""
    return GroqClient(api_key=groq_api_key)


@pytest.fixture
def cerebras_api_key() -> str:
    """Fixture that provides Cerebras API key or skips test."""
    key = get_cerebras_api_key()
    if not key:
        pytest.skip("CEREBRAS_API_KEY environment variable not set")
    return key


@pytest.fixture
def cerebras_client(cerebras_api_key: str) -> CerebrasClient:
    """Fixture that provides a configured Cerebras client."""
    return CerebrasClient(api_key=cerebras_api_key)


@pytest.fixture
def anthropic_api_key() -> str:
    """Fixture that provides Anthropic API key or skips test."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY environment variable not set")
    return key


@pytest.fixture
def openai_api_key() -> str:
    """Fixture that provides OpenAI API key or skips test."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        pytest.skip("OPENAI_API_KEY environment variable not set")
    return key


@pytest.fixture
def anthropic_client(anthropic_api_key: str) -> AnthropicClient:
    """Fixture that provides a configured Anthropic client."""
    return AnthropicClient(api_key=anthropic_api_key)
