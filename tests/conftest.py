"""Shared test fixtures and tier auto-marking.

Tiers are marked by path (DESIGN §10): tests/unit/** is `unit`;
tests/external/<provider>/** is `external` + `external_<provider>`;
tests/external/cross/** needs several providers' keys and carries all four.
Selection is by marker — addopts exclude `external` by default.
"""

from pathlib import Path

import pytest

_PROVIDERS = ("groq", "openai", "anthropic", "cerebras")
_TESTS_DIR = Path(__file__).parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        rel = Path(item.fspath).relative_to(_TESTS_DIR).parts
        if rel[0] == "unit":
            item.add_marker(pytest.mark.unit)
        elif rel[0] == "external":
            item.add_marker(pytest.mark.external)
            suites = _PROVIDERS if rel[1] == "cross" else (rel[1],)
            for suite in suites:
                item.add_marker(getattr(pytest.mark, f"external_{suite}"))


@pytest.fixture
def sample_system_prompt() -> str:
    """Sample system prompt for testing."""
    return "You are a helpful assistant."
