"""Shared test fixtures and tier auto-marking.

Tiers are marked by path (DESIGN §10): tests/unit/** is `unit`;
tests/external/<provider>/** is `external` + `external_<provider>`;
tests/external/cross/** is parametrized per provider and self-skips per
key, so it carries every suite's marker. Selection is by marker —
addopts exclude `external` by default. The addopts ceiling (60 s, TP-12)
is the unit tier's hang detector over fakes; an external item carries
its own — a real-API baseline runs minutes (the Kimi lane most of an
hour), so its ceiling is an hour per test, inside the job's 180.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

# The three shipped adapters, then the door lanes (tests/external/lanes.py).
_PROVIDERS = (
    "openai",
    "anthropic",
    "cerebras",
    "xai",
    "gemini",
    "kimi",
    "deepseek",
    "qwen",
)
_TESTS_DIR = Path(__file__).parent
_EXTERNAL_TIMEOUT_SECONDS = 3600
_KEYS_AT_START: set[str] = set()


def _provider_keys() -> set[str]:
    return {name for name in os.environ if name.endswith("_API_KEY")}


def pytest_sessionstart() -> None:
    _KEYS_AT_START.update(_provider_keys())


def pytest_collection_finish() -> None:
    """A provider key reaches a test only from the operator's environment
    (DESIGN §10): an import at collection that loads one — an example's
    module-level app reading `~/.neosian/config.toml` did — defeats every
    self-skip after it and spends the developer's account. Names only."""
    leaked = sorted(_provider_keys() - _KEYS_AT_START)
    if leaked:
        pytest.exit(f"collection put provider keys in the environment: {leaked}", 3)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        rel = Path(item.fspath).relative_to(_TESTS_DIR).parts
        if rel[0] == "unit":
            item.add_marker(pytest.mark.unit)
        elif rel[0] == "external":
            item.add_marker(pytest.mark.external)
            item.add_marker(pytest.mark.timeout(_EXTERNAL_TIMEOUT_SECONDS))
            suites = _PROVIDERS if rel[1] == "cross" else (rel[1],)
            for suite in suites:
                item.add_marker(getattr(pytest.mark, f"external_{suite}"))


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The home (DESIGN §22) is every entry's default store, so a test that
    passes no store flag would otherwise reach the developer's real
    `~/.neosian`; each test gets its own under tmp_path."""
    monkeypatch.setenv("NEOSIAN_HOME", str(tmp_path / "home"))


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
