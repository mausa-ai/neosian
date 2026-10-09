"""`RemoteStore`'s side of the eraser (N8, DESIGN §38): the two
`conversation/*` erasure routes as the `Erasable` protocol, one POST
each, honest only over a backend that erases: the handshake transmits
whether it does, and an act asked of one that does not is refused by
name before any request (#109's rule, the `Pageable` precedent)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from neosian._foundation.conversation.erasable import check_trail_window
from neosian._foundation.server.wire import decode_turn_redaction, encode_timestamp
from neosian._foundation.shared.exceptions import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from neosian._foundation.conversation.types import ConversationRedaction


class RemoteErasable:
    """Mixin; the host class owns `_call` and `_erasable` (`remote.py`)."""

    _erasable: bool

    if TYPE_CHECKING:

        async def _call(
            self, endpoint: str, payload: dict[str, Any]
        ) -> dict[str, Any]: ...

    def _erasure(self) -> None:
        if not self._erasable:
            raise ConfigurationError(
                "the server's backend does not implement Erasable — its turns "
                "cannot be redacted (DESIGN §38)"
            )

    async def redact_turns(
        self,
        conversation_id: str,
        *,
        through: int | None = None,
        turns: Sequence[int] | None = None,
        actor: str | None = None,
    ) -> int:
        self._erasure()
        data = await self._call(
            "conversation/redact_turns",
            {
                "conversation_id": conversation_id,
                "through": through,
                "turns": None if turns is None else list(turns),
                "actor": actor,
            },
        )
        return int(data["count"])

    async def turn_redactions(
        self,
        *,
        conversations: Sequence[str] | None = None,
        since: datetime | None = None,
        limit: int = 50,
    ) -> tuple[ConversationRedaction, ...]:
        self._erasure()
        check_trail_window(since, limit)  # a naive `since` never crosses (C4)
        data = await self._call(
            "conversation/turn_redactions",
            {
                "conversations": None if conversations is None else list(conversations),
                "since": None if since is None else encode_timestamp(since),
                "limit": limit,
            },
        )
        return tuple(decode_turn_redaction(act) for act in data["redactions"])
