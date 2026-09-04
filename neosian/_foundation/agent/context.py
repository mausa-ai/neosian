"""Per-run execution context (DESIGN §3).

`RunContext` carries everything one run needs that differs between
`Agent.run` and `AgentSession.run`: the client-acquisition seam, the
session's sticky fallback state, the hook dispatcher — and the run's one
usage ledger, shared by every attempt the run makes. The
`*_with_session` method twins this replaced differed in nothing else
(machine-verified before the N0 collapse).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from neosian._foundation.llm.base import Message, ModelUsage, Usage
from neosian._foundation.shared.types import AnyModel, ClientFactory, FallbackState

if TYPE_CHECKING:
    from neosian._foundation.agent.base import Agent
    from neosian._foundation.agent.hooks import HookRunner


def merge_usage(a: Usage | None, b: Usage | None) -> Usage | None:
    """Sum two optional Usage values; None+None stays None."""
    if a is None:
        return b
    if b is None:
        return a
    return a + b


@dataclass(slots=True)
class UsageLedger:
    """Everything one run spent, keyed by API-reported model.

    One ledger per run: every attempt records into it (a failed attempt's
    tokens were billed), and so does the guardrail classifier — the
    "never undercount" rule needs one place every terminal value reads.
    """

    _by_model: dict[str, Usage] = field(default_factory=dict)

    def record(self, key: str, usage: Usage) -> None:
        """Fold one completion's usage in, first-appearance order."""
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


@dataclass(frozen=True, slots=True)
class RunContext:
    """One run's wiring; construction is `Agent._run_context`'s job.

    `acquire` IS the session-twin seam: Agent passes `_create_client`
    (fresh client per attempt), a session passes `_get_or_create_client`
    (cached per provider). `fallback_state` is None outside sessions —
    sticky fallback is session behavior.
    """

    agent: Agent
    acquire: ClientFactory
    hooks: HookRunner
    fallback_state: FallbackState | None = None
    started: float = field(default_factory=time.monotonic)  # TurnEvent duration
    ledger: UsageLedger = field(default_factory=UsageLedger)


@dataclass(slots=True)
class Attempt:
    """One (model, client) try: its own message list over the run's ledger.

    `messages` is a fresh copy of the base conversation, so a failed
    attempt is discarded whole — a fallback attempt never inherits the
    failed main attempt's partial tool rounds (DESIGN §3 found-bug
    register #5). The usage ledger, by contrast, is the run's: a failed
    attempt's tokens were still billed, and the run's terminal value
    (response or error) must carry everything the run spent, split per
    API-reported model.
    """

    model: AnyModel
    messages: list[Message]
    base_len: int
    ledger: UsageLedger

    @classmethod
    def start(
        cls, model: AnyModel, base_messages: list[Message], ledger: UsageLedger
    ) -> Attempt:
        """A fresh attempt over `base_messages`, recording into `ledger`."""
        return cls(
            model=model,
            messages=list(base_messages),
            base_len=len(base_messages),
            ledger=ledger,
        )

    def record(self, api_model: str | None, usage: Usage) -> None:
        """Fold one completion's usage into the run's ledger.

        Keyed by the API-reported model string; falls back to the
        requested model when the API reported none.
        """
        self.ledger.record(api_model or self.model.value, usage)

    @property
    def usage(self) -> Usage | None:
        """The run's summed ledger; None when nothing was billed yet."""
        return self.ledger.usage

    @property
    def usage_by_model(self) -> tuple[ModelUsage, ...]:
        """The run's per-model split, first-appearance order."""
        return self.ledger.usage_by_model

    def turn_messages(self, final: Message) -> tuple[Message, ...]:
        """The turn's provider-order messages; `final` is the last element
        by identity, so `turn_messages[-1] is response.message` holds."""
        return (*self.messages[self.base_len :], final)
