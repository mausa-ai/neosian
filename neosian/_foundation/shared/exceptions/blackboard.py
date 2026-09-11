"""The retired blackboard's codes, kept append-only (DESIGN §21, ECOSYSTEM §6)."""

from __future__ import annotations

from neosian._foundation.shared.exceptions.base import NeosianError


# Blackboard Errors — the subsystem retired at NB (DESIGN §21); the codes
# stay in the table append-only (ECOSYSTEM §6) and nothing raises them.
class BlackboardError(NeosianError):
    """Base of the retired blackboard family; never raised since NB."""

    code = "blackboard_error"


class BlackboardEntryNotFoundError(BlackboardError):
    """Retired; the code stays."""

    code = "blackboard_entry_not_found"


class BlackboardReadError(BlackboardError):
    """Retired; the code stays."""

    code = "blackboard_read_failed"


class BlackboardUpdateError(BlackboardError):
    """Retired; the code stays."""

    code = "blackboard_update_failed"


class FileBlackboardDirectoryNotFoundError(BlackboardError):
    """Retired; the code stays."""

    code = "blackboard_directory_not_found"
