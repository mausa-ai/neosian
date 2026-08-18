"""Fixtures for external tests (real API calls).

Each provider suite reads its key from the environment and self-skips when it
is absent. Checks test falsiness, not None — an absent CI secret arrives as
the empty string. Inject credentials value-blind via scripts/external_env.py:

    make test-external provider=groq file=~/path/to/creds
"""

import os

import pytest

from neosian._foundation.llm.anthropic import AnthropicClient
from neosian._foundation.llm.cerebras import CerebrasClient
from neosian._foundation.llm.groq import GroqClient


def _key_or_skip(name: str) -> str:
    """Return the env key's value, or skip the test when it is falsy."""
    key = os.environ.get(name, "")
    if not key:
        pytest.skip(f"{name} not set")
    return key


@pytest.fixture
def groq_api_key() -> str:
    return _key_or_skip("GROQ_API_KEY")


@pytest.fixture
def openai_api_key() -> str:
    return _key_or_skip("OPENAI_API_KEY")


@pytest.fixture
def anthropic_api_key() -> str:
    return _key_or_skip("ANTHROPIC_API_KEY")


@pytest.fixture
def cerebras_api_key() -> str:
    return _key_or_skip("CEREBRAS_API_KEY")


@pytest.fixture
def groq_client(groq_api_key: str) -> GroqClient:
    return GroqClient(api_key=groq_api_key)


@pytest.fixture
def cerebras_client(cerebras_api_key: str) -> CerebrasClient:
    return CerebrasClient(api_key=cerebras_api_key)


@pytest.fixture
def anthropic_client(anthropic_api_key: str) -> AnthropicClient:
    return AnthropicClient(api_key=anthropic_api_key)
