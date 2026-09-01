"""Per-run execution context (DESIGN §3).

`RunContext` carries everything one run needs that differs between
`Agent.run` and `AgentSession.run`: the client-acquisition seam, the
session's sticky fallback state, and the hook dispatcher. The
`*_with_session` method twins this replaced differed in nothing else
(machine-verified before the N0 collapse).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from neosian._foundation.llm.base import BaseLLMClient, Message, ModelUsage, Usage
from neosian._foundation.shared.types import AnyModel, FallbackState

if TYPE_CHECKING:
    from neosian._foundation.agent.base import Agent
    from neosian._foundation.agent.hooks import HookRunner

ClientAcquire = Callable[[AnyModel], BaseLLMClient]


def merge_usage(a: Usage | None, b: Usage | None) -> Usage | None:
    """Sum two optional Usage values; None+None stays None."""
    if a is None:
        return b
    if b is None:
        return a
    return a + b


@dataclass(frozen=True, slots=True)
class RunContext:
    """One run's wiring; construction is `Agent._run_context`'s job.

    `acquire` IS the session-twin seam: Agent passes `_create_client`
    (fresh client per attempt), a session passes `_get_or_create_client`
    (cached per provider). `fallback_state` is None outside sessions —
    sticky fallback is session behavior.
    """

    agent: Agent
    acquire: ClientAcquire
    hooks: HookRunner
    fallback_state: FallbackState | None = None
    started: float = 0.0  # time.monotonic() at run entry; TurnEvent duration


@dataclass(slots=True)
class Attempt:
    """One (model, client) try: its own message list and its own usage ledger.

    `messages` is a fresh copy of the base conversation, so a failed
    attempt is discarded whole — a fallback attempt never inherits the
    failed main attempt's partial tool rounds (DESIGN §3 found-bug
    register #5). The usage ledger, by contrast, IS inherited across
    attempts via `start(prior=...)`: a failed attempt's tokens were still
    billed, and the run's terminal value (response or error) must carry
    everything the run spent, split per API-reported model.
    """

    model: AnyModel
    messages: list[Message]
    base_len: int
    _by_model: dict[str, Usage] = field(default_factory=dict)

    @classmethod
    def start(
        cls,
        model: AnyModel,
        base_messages: list[Message],
        prior: Attempt | None = None,
    ) -> Attempt:
        """A fresh attempt over `base_messages`; ledger carried from `prior`."""
        return cls(
            model=model,
            messages=list(base_messages),
            base_len=len(base_messages),
            _by_model=dict(prior._by_model) if prior is not None else {},
        )

    def record(self, api_model: str | None, usage: Usage) -> None:
        """Fold one completion's usage into the ledger.

        Keyed by the API-reported model string, first-appearance order;
        falls back to the requested model when the API reported none.
        """
        key = api_model or self.model.value
        existing = self._by_model.get(key)
        self._by_model[key] = usage if existing is None else existing + usage

    @property
    def usage(self) -> Usage | None:
        """Summed ledger; None when nothing was billed yet."""
        total: Usage | None = None
        for entry in self._by_model.values():
            total = entry if total is None else total + entry
        return total

    @property
    def usage_by_model(self) -> tuple[ModelUsage, ...]:
        """The per-model split, first-appearance order."""
        return tuple(
            ModelUsage(model=model, usage=usage)
            for model, usage in self._by_model.items()
        )

    def turn_messages(self, final: Message) -> tuple[Message, ...]:
        """The turn's provider-order messages; `final` is the last element
        by identity, so `turn_messages[-1] is response.message` holds."""
        return (*self.messages[self.base_len :], final)
