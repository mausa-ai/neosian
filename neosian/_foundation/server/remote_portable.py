"""`RemoteStore`'s side of store mobility (NC4, §26): the four `store/*`
routes as the `Portable` protocol, one POST each, one unit per request
(the body ceiling and the client timeout bound a unit, not a store)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from neosian._foundation.server.wire_archive import (
    encode_conversation_archive,
    encode_scope_archive,
)

if TYPE_CHECKING:
    from neosian._foundation.memory.portable import ConversationArchive, ScopeArchive


class RemotePortable:
    """Mixin; the host class owns `_call` (`remote.py`)."""

    if TYPE_CHECKING:

        async def _call(
            self, endpoint: str, payload: dict[str, Any]
        ) -> dict[str, Any]: ...

    async def scopes(self) -> tuple[str, ...]:
        data = await self._call("store/scopes", {})
        return tuple(str(scope) for scope in data["scopes"])

    async def conversations(self) -> tuple[str, ...]:
        data = await self._call("store/conversations", {})
        return tuple(str(name) for name in data["conversations"])

    async def restore_scope(self, archive: ScopeArchive) -> None:
        await self._call("store/restore_scope", encode_scope_archive(archive))

    async def restore_conversation(self, archive: ConversationArchive) -> None:
        await self._call(
            "store/restore_conversation", encode_conversation_archive(archive)
        )
