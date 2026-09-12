"""Tests for the ConversationStore ABC (conversation/base.py)."""

import inspect

import pytest

from neosian._foundation.conversation.base import ConversationStore

_METHODS = (
    "append_turn",
    "read_turns",
    "last_turn_number",
    "append_projections",
    "read_projections",
)


class TestConversationStoreABC:
    def test_cannot_be_instantiated(self) -> None:
        with pytest.raises(TypeError):
            ConversationStore()  # type: ignore[abstract]

    def test_declares_exactly_the_five_methods(self) -> None:
        assert set(ConversationStore.__abstractmethods__) == set(_METHODS)

    def test_every_method_is_an_async_def(self) -> None:
        for name in _METHODS:
            assert inspect.iscoroutinefunction(getattr(ConversationStore, name)), name

    def test_owns_no_init(self) -> None:
        # CS1: no __init__ in the ABC — construction is the implementation's.
        assert "__init__" not in ConversationStore.__dict__

    def test_declares_no_capability_classvar(self) -> None:
        # Nothing varies by substrate; concurrency is answered normatively
        # by CS3 (DESIGN §9.2).
        assert not hasattr(ConversationStore, "supports_optimistic_concurrency")

    def test_list_conversations_is_reserved_never_shipped(self) -> None:
        # NQ2, ledger #229: reserved for a 1.x minor, arriving only through
        # an ECOSYSTEM §12 pair. Hosts list from their own tables; the
        # library's own enumeration is the Portable privilege (§26).
        assert not hasattr(ConversationStore, "list_conversations")
