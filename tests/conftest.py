"""Shared test fixtures and tier auto-marking.

Tiers are marked by path (DESIGN §10): tests/unit/** is `unit`;
tests/external/<provider>/** is `external` + `external_<provider>`;
tests/external/cross/** is parametrized per provider and self-skips per
key, so it carries every suite's marker. Selection is by marker —
addopts exclude `external` by default.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

# The three shipped adapters, then the candidate doors of NC2 slice B.
_PROVIDERS = (
    "openai",
    "anthropic",
    "cerebras",
    "xai",
    "gemini",
    "deepseek",
    "qwen",
    "kimi",
)
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


@pytest.fixture(autouse=True)
def _isolated_model_registry() -> Iterator[None]:
    """Registrations are process-global configuration (DESIGN §19); each
    test starts from the registry it found and leaves it as found."""
    from neosian._foundation.shared.registry import _REGISTRY

    before = dict(_REGISTRY)
    yield
    _REGISTRY.clear()
    _REGISTRY.update(before)
