"""Fixtures for external tests (real API calls).

Each provider suite reads its key from the environment and self-skips when it
is absent. Checks test falsiness, not None — an absent CI secret arrives as
the empty string. Inject credentials value-blind via scripts/external_env.py:

    make test-external provider=anthropic file=~/path/to/creds
"""

import os

import pytest

from neosian._foundation.llm.anthropic import AnthropicClient
from neosian._foundation.llm.openai import OpenAICompatibleClient
from neosian._foundation.shared.catalog import CEREBRAS


def _key_or_skip(name: str) -> str:
    """Return the env key's value, or skip the test when it is falsy."""
    key = os.environ.get(name, "")
    if not key:
        pytest.skip(f"{name} not set")
    return key


@pytest.fixture
def openai_api_key() -> str:
    return _key_or_skip("OPENAI_API_KEY")


@pytest.fixture
def anthropic_api_key() -> str:
    return _key_or_skip("ANTHROPIC_API_KEY")


@pytest.fixture
def cerebras_api_key() -> str:
    return _key_or_skip("CEREBRAS_API_KEY")


# The door lanes (tests/external/lanes.py) — keys only; the door client
# is built from the lane where it is probed.


@pytest.fixture
def xai_api_key() -> str:
    return _key_or_skip("XAI_API_KEY")


@pytest.fixture
def gemini_api_key() -> str:
    return _key_or_skip("GEMINI_API_KEY")


@pytest.fixture
def kimi_api_key() -> str:
    return _key_or_skip("MOONSHOT_API_KEY")


@pytest.fixture
def deepseek_api_key() -> str:
    return _key_or_skip("DEEPSEEK_API_KEY")


@pytest.fixture
def qwen_api_key() -> str:
    return _key_or_skip("DASHSCOPE_API_KEY")


@pytest.fixture
def postgres_dsn() -> str:
    """A live PostgreSQL server — a DSN, not an API key (SERVICES.md)."""
    return _key_or_skip("NEOSIAN_TEST_POSTGRES_DSN")


@pytest.fixture
def cerebras_client(cerebras_api_key: str) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(api_key=cerebras_api_key, door=CEREBRAS)


@pytest.fixture
def anthropic_client(anthropic_api_key: str) -> AnthropicClient:
    return AnthropicClient(api_key=anthropic_api_key)
