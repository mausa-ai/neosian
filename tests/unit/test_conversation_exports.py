"""The neosian.conversation facade: pinned surface, lazily loaded
(DESIGN §1, §9.9)."""

import subprocess
import sys

import pytest


@pytest.mark.unit
def test_conversation_all_is_pinned() -> None:
    import neosian.conversation

    assert neosian.conversation.__all__ == [
        "CONVERSATION_FORMAT_VERSION",
        "CONVERSATION_ID_MAX_LENGTH",
        "CONVERSATION_ID_PATTERN",
        "DEFAULT_MEMORY_MOUNT_PATH",
        "CompactionConfig",
        "CompactionResult",
        "Conversation",
        "ConversationFormatUnsupportedError",
        "ConversationId",
        "ConversationIdInvalidError",
        "ConversationProjection",
        "ConversationStore",
        "ConversationStoreError",
        "ConversationTurn",
        "FileStore",
        "PostgresStore",
        "ProjectionKind",
        "ReflectionConfig",
        "ReflectionResult",
        "ReflectionWrite",
        "RemoteStore",
        "create_recall_turn_tool",
        "message_from_json",
        "message_to_json",
        "parse_conversation_id",
    ]
    for name in neosian.conversation.__all__:
        assert getattr(neosian.conversation, name) is not None


@pytest.mark.unit
def test_conversation_testing_exports_the_contract_kit() -> None:
    import neosian.conversation.testing

    assert neosian.conversation.testing.__all__ == ["ConversationStoreContract"]


@pytest.mark.unit
def test_root_import_does_not_load_conversation_facade() -> None:
    """`import neosian` must not pull in neosian.conversation."""
    code = "import neosian, sys; assert 'neosian.conversation' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.unit
def test_conversation_facade_does_not_load_the_testing_kit() -> None:
    """pytest must never become a runtime dependency of neosian.conversation."""
    code = (
        "import neosian.conversation, sys; "
        "assert 'neosian.conversation.testing' not in sys.modules; "
        "assert 'neosian._foundation.conversation.testing' not in sys.modules; "
        "assert 'pytest' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
