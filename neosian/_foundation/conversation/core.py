"""Conversation — the opt-in stateful shell around the stateless core.

The two-key model (DESIGN §9): `conversation_id` keys history, memory keys
off mounts. One `send()` appends exactly one turn — the USER message plus
every message the run produced; resume is constructing with the same
`conversation_id`. The persistence seam is `on_turn` (the only point where
blocking and streaming agree): the hook captures, `send()` writes.

Compaction (§9.6) lives here: the prompt is a rendered *view* of the
append-only history, the high-water trigger runs before the agent call,
and the boundary — projection, index refresh, lazy `recall_turn`
registration — rebuilds the derived agent. Spend folds into the returned
response or terminal event; compaction never loses the send.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Literal, Self, overload

from neosian._foundation.agent.base import Agent
from neosian._foundation.agent.session import AgentSession
from neosian._foundation.conversation.compaction import (
    CompactionConfig,
    CompactionResult,
    run_boundary,
    should_compact,
)
from neosian._foundation.conversation.ids import parse_conversation_id
from neosian._foundation.conversation.projection import render_view
from neosian._foundation.conversation.recall import create_recall_turn_tool
from neosian._foundation.conversation.wiring import (
    DEFAULT_MEMORY_MOUNT_PATH,
    derive_config,
    fold_event,
    fold_response,
    resolve_memory,
)
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.memory.index import memory_system_section
from neosian._foundation.shared.context_policy import ContextPolicy

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from types import TracebackType

    from neosian._foundation.agent.events import AgentEvent
    from neosian._foundation.agent.hooks import TurnEvent
    from neosian._foundation.agent.response import AgentResponse
    from neosian._foundation.conversation.base import ConversationStore
    from neosian._foundation.conversation.types import (
        ConversationProjection,
        ConversationTurn,
    )
    from neosian._foundation.memory.mounts import MemoryConfig, Mount
    from neosian._foundation.shared.types import AgentConfig, ToolFunction

logger = logging.getLogger(__name__)


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

    Compaction is default-on (ledger #28); `CompactionConfig(enabled=
    False)` disables the automatic trigger, `compact()` always runs.

    One internal `AgentSession` backs every send *and* compaction's
    distillation calls — one cached client per provider, and sticky
    fallback state across sends (§9.5.14). `aclose()` releases the pool;
    `async with conversation:` is the sugar. Not closing is safe: the
    pool lives as long as the process, exactly like an unclosed
    `AgentSession`.
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
        compaction: CompactionConfig | None = None,
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
        self._compaction = compaction if compaction is not None else CompactionConfig()
        if self._base_config.server_compaction:
            # Log-projection replaces aged turns with log lines, dropping
            # any server compaction blocks they carried — the server would
            # then re-compact (and re-bill) the same span every send.
            logger.warning(
                "server_compaction is on under a Conversation — the view's "
                "log-projection drops server compaction blocks at the warm "
                "boundary, paying for the same compaction repeatedly; "
                "Conversation's own paging is the supported path (§9.6)"
            )
        self._lock = asyncio.Lock()
        self._turns: list[ConversationTurn] = []
        self._projections: list[ConversationProjection] = []
        self._agent: Agent | None = None
        self._session: AgentSession | None = None
        self._captured: AgentResponse | None = None

    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    @property
    def messages(self) -> tuple[Message, ...]:
        """The verbatim history — empty before the conversation starts."""
        return tuple(m for turn in self._turns for m in turn.messages)

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
        """Run one turn against the rendered view and persist it.

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

    async def compact(self) -> CompactionResult:
        """Run one compaction boundary now (the manual `/compact` idiom).

        Skips the high-water check and ignores `enabled` — an explicit
        call is explicit intent — but still respects `hot_turns`. Returns
        the checkpointed entries and the model spend; empty entries mean
        there was nothing to project.
        """
        async with self._lock:
            await self._ensure_started()
            if not self._compaction.enabled and not self._projections:
                self._projections = list(
                    await self._store.read_projections(self._conversation_id)
                )
            return await self._run_boundary()

    async def aclose(self) -> None:
        """Release the client pool; idempotent.

        A release, not a destroy: a later `send()` opens a fresh pool.
        Deliberately does not take the send lock — waiting on it would
        deadlock on a stream the consumer abandoned (the generator holds
        the lock until collected). Closing under a live stream fails
        that stream; finish or abandon it first.
        """
        session, self._session = self._session, None
        if session is not None:
            await session.close()

    async def __aenter__(self) -> Self:
        # No I/O — lazy start (§9.5.9) stays literally true; call
        # `start()` explicitly to surface store errors early.
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    # Internal plumbing ----------------------------------------------------

    def _capture(self, event: TurnEvent) -> None:
        self._captured = event.response

    async def _ensure_started(self) -> None:
        if self._agent is not None:
            return
        self._turns = list(await self._store.read_turns(self._conversation_id))
        if self._compaction.enabled:
            # A disabled config ignores projections left by an earlier
            # enabled run — and keeps the non-compacting path at exactly
            # one store read.
            self._projections = list(
                await self._store.read_projections(self._conversation_id)
            )
        await self._rebuild_agent()

    async def _rebuild_agent(self) -> None:
        """Derive the agent: at start, and again at each compaction
        boundary — the one legitimate memory-index refresh (§9.5.10)."""
        section = None
        if self._memory_config is not None:
            section = await memory_system_section(self._memory_config)
        extra_tools: list[ToolFunction] = []
        if self._compaction.recall_tool and self._projections:
            # Lazy registration (ledger #28): the tool appears in the
            # same request as the first log block that references it.
            extra_tools.append(
                create_recall_turn_tool(self._store, self._conversation_id)
            )
        derived = derive_config(
            self._base_config,
            section=section,
            memory_config=self._memory_config,
            actor=self._conversation_id,
            capture=self._capture,
            extra_tools=extra_tools,
        )
        if self._max_tool_iterations is None:
            self._agent = Agent(derived)
        else:
            self._agent = Agent(derived, self._max_tool_iterations)
        if self._session is not None:
            self._session._rebind(self._agent)

    def _session_for_run(self) -> AgentSession:
        """The one client pool: sends and distillation share it, and a
        boundary rebuild rebinds it instead of reconnecting."""
        assert self._agent is not None
        if self._session is None:
            self._session = AgentSession(self._agent)
        return self._session

    def _view(self) -> list[Message]:
        return render_view(self._turns, self._projections)

    async def _run_boundary(self) -> CompactionResult:
        assert self._agent is not None
        result = await run_boundary(
            store=self._store,
            conversation_id=self._conversation_id,
            turns=self._turns,
            projections=self._projections,
            config=self._compaction,
            model=self._base_config.model,
            acquire=self._session_for_run()._get_or_create_client,
        )
        if result.entries:
            self._projections.extend(result.entries)
            await self._rebuild_agent()
        return result

    async def _maybe_compact(
        self, view: list[Message], user: Message
    ) -> CompactionResult | None:
        if not self._compaction.enabled:
            return None
        assert self._agent is not None
        # `context_policy=None` disables the pre-call raise, never paging
        # (ledger #30); the estimate includes the derived system prompt.
        policy = self._base_config.context_policy or ContextPolicy()
        probe = [
            Message(role=Role.SYSTEM, content=str(self._agent.config.system_prompt)),
            *view,
            user,
        ]
        if not should_compact(
            probe,
            policy=policy,
            model=self._base_config.model,
            fraction=self._compaction.trigger_fraction,
        ):
            return None
        return await self._run_boundary()

    def _capture_pending(self) -> bool:
        # A method, not an inline check: the on_turn hook mutates
        # `_captured` across awaits, which mypy's attribute narrowing
        # cannot see (an inline `is not None` is proven unreachable).
        return self._captured is not None

    async def _persist(self, user: Message) -> None:
        captured = self._captured
        self._captured = None
        if captured is None or not captured.turn_messages:
            return
        turn = await self._store.append_turn(
            self._conversation_id, (user, *captured.turn_messages)
        )
        self._turns.append(turn)

    async def _send_blocking(self, user: Message) -> AgentResponse:
        async with self._lock:
            await self._ensure_started()
            view = self._view()
            compacted = await self._maybe_compact(view, user)
            if compacted is not None and compacted.entries:
                view = self._view()
            assert self._agent is not None
            self._captured = None
            response = await self._session_for_run().run([*view, user], stream=False)
            await self._persist(user)
            return fold_response(response, compacted)

    async def _send_streaming(self, user: Message) -> AsyncIterator[AgentEvent]:
        async with self._lock:
            await self._ensure_started()
            view = self._view()
            compacted = await self._maybe_compact(view, user)
            if compacted is not None and compacted.entries:
                view = self._view()
            assert self._agent is not None
            self._captured = None
            events = await self._session_for_run().run([*view, user], stream=True)
            async for event in events:
                # The capture hook fires before the terminal event is
                # yielded (register #6), so persisting here — before the
                # relay — makes "consumer saw the terminal ⇒ turn
                # persisted" unconditional, even for a consumer that
                # stops iterating at `done`. A store failure surfaces in
                # place of the terminal event. The guard keeps an awaited
                # call off the per-delta hot path: _persist runs only
                # once the capture lands, and clears it when written.
                if self._capture_pending():
                    await self._persist(user)
                yield fold_event(event, compacted)
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
