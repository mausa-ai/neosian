"""Fixtures for integration tests.

Integration tests require real API keys set as environment variables:
- GROQ_API_KEY: Required for Groq LLM tests

Run with: GROQ_API_KEY=gsk_xxx uv run pytest -m integration -v
"""

import os

import pytest

from neosian._foundation.llm.groq import GroqClient


def get_groq_api_key() -> str | None:
    """Get Groq API key from environment."""
    return os.environ.get("GROQ_API_KEY")


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
