"""Native conversation glue; failures do not consume or block messages."""

from __future__ import annotations

import logging
from collections.abc import Callable

from neosian._foundation.llm.base import Message, Role
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.messaging.core import Mailbox
from neosian._foundation.messaging.delivery import context
from neosian._foundation.messaging.types import MailboxConfig

logger = logging.getLogger(__name__)


def bind(
    memory: MemoryConfig | None,
    config: MailboxConfig | None,
    session: str,
    actor: Callable[[], str],
) -> Mailbox | None:
    if config is None:
        return None
    if memory is None:
        raise ValueError("mailbox requires explicitly configured memory mounts")
    return Mailbox(memory, config=config, session=session, actor=actor)


async def messages_for_turn(mailbox: Mailbox | None) -> list[Message]:
    if mailbox is None:
        return []
    try:
        text = await context(mailbox)
    except Exception:
        logger.warning("mailbox unavailable; unread messages retained", exc_info=True)
        return []
    return [Message(role=Role.USER, content=text)] if text else []
