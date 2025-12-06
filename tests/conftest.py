"""Shared test fixtures."""

import pytest


@pytest.fixture
def sample_system_prompt() -> str:
    """Sample system prompt for testing."""
    return "You are a helpful assistant."
