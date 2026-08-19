"""Conversation — the opt-in stateful shell around the stateless core.

The two-key model (DESIGN §9): `conversation_id` keys history, memory keys
off mounts. One `send()` appends exactly one turn — the USER message plus
every message the run produced; resume is constructing with the same
`conversation_id`. The persistence seam is `on_turn` (the only point where
blocking and streaming agree): the hook captures, `send()` writes.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal, Self, overload

from neosian._foundation.agent.base import Agent
from neosian._foundation.conversation.ids import parse_conversation_id
from neosian._foundation.conversation.wiring import (
    DEFAULT_MEMORY_MOUNT_PATH,
    derive_config,
    resolve_memory,
)
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.memory.index import memory_system_section

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from neosian._foundation.agent.events import AgentEvent
    from neosian._foundation.agent.hooks import TurnEvent
    from neosian._foundation.agent.response import AgentResponse
    from neosian._foundation.conversation.base import ConversationStore
    from neosian._foundation.memory.mounts import MemoryConfig, Mount
    from neosian._foundation.shared.types import AgentConfig


class Conversation:
    """Append-only, resumable history with opt-in memory (DESIGN §9).

        store = FileStore(path)                # or PostgresStore(dsn), N3
        convo = Conversation(agent, store=store,
                             conversation_id="thread-829",
                             memory_scope="user:1234")
        resp = await convo.send("Where did we leave off?")

    Construction is sync and does no I/O; the first `send()` (or an
    explicit `await start()`) loads history and freezes the memory index.
    The caller's `Agent`/`AgentConfig` is never mutated. One send is in
    flight at a time; a raising or blocked send persists nothing.
    """

    def __init__(
        self,
        agent: Agent | AgentConfig,
        *,
        store: ConversationStore,
        conversation_id: str,
        memory: MemoryConfig | None = None,
        mounts: Sequence[Mount] | None = None,
        memory_scope: str | None = None,
        memory_mount_path: str = DEFAULT_MEMORY_MOUNT_PATH,
    ) -> None:
        self._conversation_id = str(parse_conversation_id(conversation_id))
        self._store = store
        if isinstance(agent, Agent):
            self._base_config = agent.config
            self._max_tool_iterations: int | None = agent.max_tool_iterations
        else:
            self._base_config = agent
            self._max_tool_iterations = None
        self._memory_config = resolve_memory(
            store,
            self._base_config.memory,
            memory=memory,
            mounts=mounts,
            memory_scope=memory_scope,
            memory_mount_path=memory_mount_path,
        )
        self._lock = asyncio.Lock()
        self._history: list[Message] = []
        self._agent: Agent | None = None
        self._captured: AgentResponse | None = None

    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    @property
    def messages(self) -> tuple[Message, ...]:
        """The verbatim history — empty before the conversation starts."""
        return tuple(self._history)

    async def start(self) -> Self:
        """Load history and freeze the memory index; idempotent.

        Resume is construction with the same `conversation_id` — this is
        where the stored turns become the in-memory history.
        """
        async with self._lock:
            await self._ensure_started()
        return self

    @overload
    async def send(
        self, message: str | Message, *, stream: Literal[False] = False
    ) -> AgentResponse: ...

    @overload
    async def send(
        self, message: str | Message, *, stream: Literal[True]
    ) -> AsyncIterator[AgentEvent]: ...

    async def send(
        self, message: str | Message, *, stream: bool = False
    ) -> AgentResponse | AsyncIterator[AgentEvent]:
        """Run one turn against the full history and persist it.

        Blocking returns the AgentResponse; `stream=True` returns the
        typed event iterator (DESIGN §6) — the turn persists once the
        terminal event has been consumed, and abandoning the stream
        before it persists nothing. Blocked and raising runs persist
        nothing either way (§9.5).
        """
        user = _as_user_message(message)
        if stream:
            return self._send_streaming(user)
        return await self._send_blocking(user)

    # Internal plumbing ----------------------------------------------------

    def _capture(self, event: TurnEvent) -> None:
        self._captured = event.response

    async def _ensure_started(self) -> None:
        if self._agent is not None:
            return
        turns = await self._store.read_turns(self._conversation_id)
        self._history = [message for turn in turns for message in turn.messages]
        section = None
        if self._memory_config is not None:
            section = await memory_system_section(self._memory_config)
        derived = derive_config(
            self._base_config,
            section=section,
            memory_config=self._memory_config,
            actor=self._conversation_id,
            capture=self._capture,
        )
        if self._max_tool_iterations is None:
            self._agent = Agent(derived)
        else:
            self._agent = Agent(derived, self._max_tool_iterations)

    async def _persist(self, user: Message) -> None:
        captured = self._captured
        self._captured = None
        if captured is None or not captured.turn_messages:
            return
        turn = await self._store.append_turn(
            self._conversation_id, (user, *captured.turn_messages)
        )
        self._history.extend(turn.messages)

    async def _send_blocking(self, user: Message) -> AgentResponse:
        async with self._lock:
            await self._ensure_started()
            assert self._agent is not None
            self._captured = None
            response = await self._agent.run([*self._history, user], stream=False)
            await self._persist(user)
            return response

    async def _send_streaming(self, user: Message) -> AsyncIterator[AgentEvent]:
        async with self._lock:
            await self._ensure_started()
            assert self._agent is not None
            self._captured = None
            events = await self._agent.run([*self._history, user], stream=True)
            async for event in events:
                # The capture hook fires before the terminal event is
                # yielded (register #6), so persisting here — before the
                # relay — makes "consumer saw the terminal ⇒ turn
                # persisted" unconditional, even for a consumer that
                # stops iterating at `done`. A store failure surfaces in
                # place of the terminal event. _persist no-ops until the
                # capture lands, and clears it once written.
                await self._persist(user)
                yield event
            await self._persist(user)


def _as_user_message(message: str | Message) -> Message:
    if isinstance(message, Message):
        if message.role is not Role.USER:
            raise ValueError(
                f"send() takes the USER message that opens the turn; "
                f"got role {message.role.value!r}"
            )
        return message
    return Message(role=Role.USER, content=message)
