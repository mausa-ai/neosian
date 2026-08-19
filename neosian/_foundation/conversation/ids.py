"""The conversation-id grammar (DESIGN §9.4), validated — never interpreted.

One flat segment: no `/`, no `:`, bare `.`/`..` rejected, ≤ 128 chars. A
host with structure encodes it flat — the ECOSYSTEM §2 discipline applied
to the second key of the two-key model.
"""

from __future__ import annotations

import re
from typing import Final, NewType

from neosian._foundation.shared.exceptions import ConversationIdInvalidError

# A validated conversation id. Typing courtesy, not a runtime guarantee —
# stores re-validate on every call.
ConversationId = NewType("ConversationId", str)

CONVERSATION_ID_MAX_LENGTH: Final = 128

# Anchored \A…\Z: `$` would accept a trailing newline. The pattern is
# exported, so it must be safe under .match() in host code too.
CONVERSATION_ID_PATTERN: Final = re.compile(r"\A[A-Za-z0-9_.-]{1,128}\Z")


def parse_conversation_id(value: str) -> ConversationId:
    """Validate `value` against the grammar and return it as a ConversationId.

    Raises:
        ConversationIdInvalidError: On any shape violation. Never
            normalizes: case, whitespace and length are the caller's
            problem — and case-insensitive filesystems are a legal
            substrate, so ids differing only in case are not guaranteed
            distinct.
    """
    if len(value) > CONVERSATION_ID_MAX_LENGTH:
        raise ConversationIdInvalidError(
            value, f"longer than {CONVERSATION_ID_MAX_LENGTH} characters"
        )
    if not CONVERSATION_ID_PATTERN.match(value):
        raise ConversationIdInvalidError(
            value, "must match [A-Za-z0-9_.-]{1,128} — one flat segment"
        )
    if value in (".", ".."):
        raise ConversationIdInvalidError(value, f"id {value!r} is reserved")
    return ConversationId(value)
