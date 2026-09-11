"""The conversation store's errors (DESIGN §5 table, §9)."""

from __future__ import annotations

from neosian._foundation.shared.exceptions.base import NeosianError


# Conversation Errors (DESIGN §5 table, §9). Codes live under agent_ — the
# family-prefix set is closed and frozen (ECOSYSTEM §6; ledger #24).
class ConversationStoreError(NeosianError):
    """Base exception for conversation-store errors."""

    code = "agent_conversation_error"


class ConversationIdInvalidError(ConversationStoreError):
    """Raised when a conversation id violates the grammar (DESIGN §9.4)."""

    code = "agent_conversation_id_invalid"

    def __init__(self, conversation_id: str, reason: str) -> None:
        super().__init__(
            f"Invalid conversation id {conversation_id!r}: {reason}",
            details={"conversation_id": conversation_id, "reason": reason},
        )
        self.conversation_id = conversation_id
        self.reason = reason


class ConversationFormatUnsupportedError(ConversationStoreError):
    """Raised when a stored turn declares a newer format than this library reads.

    Refusal, never coercion: a newer `neosian_format`, a malformed row, or a
    naive timestamp is rejected at the boundary (DESIGN §9.2 CS6, §9.8).
    """

    code = "agent_conversation_format_unsupported"

    def __init__(self, conversation_id: str, reason: str) -> None:
        super().__init__(
            f"Unsupported turn format in conversation {conversation_id!r}: {reason}",
            details={"conversation_id": conversation_id, "reason": reason},
        )
        self.conversation_id = conversation_id
        self.reason = reason


class ConversationConflictError(ConversationStoreError):
    """Raised when a verbatim restore meets an occupied conversation (NC4).

    `reason` is machine-checkable: "target_occupied" — the conversation
    already holds turns or projections (DESIGN §26.3).
    """

    code = "agent_conversation_conflict"

    def __init__(self, conversation_id: str, reason: str) -> None:
        super().__init__(
            f"Conversation conflict on {conversation_id!r}: {reason}",
            details={"conversation_id": conversation_id, "reason": reason},
        )
        self.conversation_id = conversation_id
        self.reason = reason
