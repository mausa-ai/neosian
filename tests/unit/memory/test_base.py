"""Tests for the MemoryStore ABC (memory/base.py)."""

import inspect

import pytest

from neosian._foundation.memory.base import MemoryStore

_METHODS = (
    "read",
    "write",
    "delete",
    "rename",
    "list_documents",
    "versions",
    "redact",
    "history",
    "redactions",
)


class TestMemoryStoreABC:
    def test_cannot_be_instantiated(self) -> None:
        with pytest.raises(TypeError):
            MemoryStore()  # type: ignore[abstract]

    def test_declares_exactly_the_nine_methods(self) -> None:
        assert set(MemoryStore.__abstractmethods__) == set(_METHODS)

    def test_every_method_is_an_async_def(self) -> None:
        for name in _METHODS:
            assert inspect.iscoroutinefunction(getattr(MemoryStore, name)), name

    def test_optimistic_concurrency_defaults_to_false(self) -> None:
        assert MemoryStore.supports_optimistic_concurrency is False

    def test_owns_no_init(self) -> None:
        # C1: no __init__ in the ABC — construction is the implementation's.
        assert "__init__" not in MemoryStore.__dict__
