"""State documents are maintained by their lifecycle, not reflection."""

STATE_PREFIXES = ("messages/", "message-links/")


def state_path(path: str) -> bool:
    return path.startswith(STATE_PREFIXES)
