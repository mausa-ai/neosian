"""Public conversation surface (DESIGN §9). Import as `neosian.conversation`.

Re-exports only — the implementation lives in _foundation.conversation.
The core names are also on the root package; this module adds the full
error family, the id grammar, and `FileStore` (which implements both
storage seams). It never imports `.testing` (the conformance kit needs
pytest, which is not a runtime dependency).
"""

from neosian._foundation.conversation.base import ConversationStore
from neosian._foundation.conversation.compaction import (
    CompactionConfig,
    CompactionResult,
)
from neosian._foundation.conversation.core import Conversation
from neosian._foundation.conversation.ids import (
    CONVERSATION_ID_MAX_LENGTH,
    CONVERSATION_ID_PATTERN,
    ConversationId,
    parse_conversation_id,
)
from neosian._foundation.conversation.recall import create_recall_turn_tool
from neosian._foundation.conversation.types import (
    CONVERSATION_FORMAT_VERSION,
    ConversationProjection,
    ConversationTurn,
    ProjectionKind,
)
from neosian._foundation.conversation.wiring import DEFAULT_MEMORY_MOUNT_PATH
from neosian._foundation.llm.codec import message_from_json, message_to_json
from neosian._foundation.memory.file import FileStore
from neosian._foundation.shared.exceptions import (
    ConversationFormatUnsupportedError,
    ConversationIdInvalidError,
    ConversationStoreError,
)

__all__ = [
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
    "ProjectionKind",
    "create_recall_turn_tool",
    "message_from_json",
    "message_to_json",
    "parse_conversation_id",
]
