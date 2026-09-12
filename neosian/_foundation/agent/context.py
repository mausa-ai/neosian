"""Per-run execution context (DESIGN §3).

`RunContext` carries everything one run needs that differs between
`Agent.run` and `AgentSession.run`: the client-acquisition seam, the
session's sticky fallback state, the hook dispatcher — and the run's one
usage ledger, shared by every attempt the run makes. The
`*_with_session` method twins this replaced differed in nothing else
(machine-verified before the N0 collapse).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from neosian._foundation.agent.tool_scope import ToolScope
from neosian._foundation.llm.base import Message, ModelUsage, Usage
from neosian._foundation.shared.types import AnyModel, ClientFactory, FallbackState

if TYPE_CHECKING:
    from neosian._foundation.agent.base import Agent
    from neosian._foundation.agent.hooks import HookRunner

logger = logging.getLogger(__name__)

_UNPRICED_UNDER_COST_CAP = (
    "Model {model} has no verified pricing: its spend cannot count against "
    "max_cost_micro_usd. Use max_total_tokens to bound this run."
)


def merge_usage(a: Usage | None, b: Usage | None) -> Usage | None:
    """Sum two optional Usage values; None+None stays None."""
    if a is None:
        return b
    if b is None:
        return a
    return a + b


@dataclass(slots=True)
class UsageLedger:
    """Everything one run spent, keyed by API-reported model — and the
    ceiling it may not cross.

    One ledger per run: every attempt records into it (a failed attempt's
    tokens were billed), and so does the guardrail classifier — the
    "never undercount" rule needs one place every terminal value reads.
    That makes it the one place a budget can be enforced by construction:
    the rail covers both paths, every fallback leg and the classifier's
    own call, with no per-loop parity to keep (DESIGN §3, ledger #222).

    Folding and checking are deliberately two calls. `record` never
    raises: one site folds a mid-turn remainder inside an exception
    handler (`stream_loop`), where raising would mask the original error.
    """

    _by_model: dict[str, Usage] = field(default_factory=dict)
    # The run's ceilings; None disables that rail. Both are read off
    # AgentConfig by `Agent._run_context`.
    max_cost_micro_usd: int | None = None
    max_total_tokens: int | None = None
    _spent_micro_usd: int = 0
    _warned_unpriced: bool = False

    def record(self, key: str, usage: Usage, model: AnyModel | None = None) -> None:
        """Fold one completion's usage in, first-appearance order.

        `model` is the *requested* model, needed only to price the fold:
        the ledger keys by the API-reported string, which carries no
        pricing of its own. A model with no verified pricing contributes
        nothing to the cost tally (`max_total_tokens` is the rail that
        always fires) and says so once per run.
        """
        existing = self._by_model.get(key)
        self._by_model[key] = usage if existing is None else existing + usage
        if self.max_cost_micro_usd is None or model is None:
            return
        cost = usage.cost_micro_usd(model)
        if cost is None:
            if not self._warned_unpriced:
                self._warned_unpriced = True
                logger.warning(_UNPRICED_UNDER_COST_CAP.format(model=model.value))
            return
        # Per-fold ceiling division sums to slightly more than the ceiling
        # of the sum — the safe direction for a cap (ECOSYSTEM §4).
        self._spent_micro_usd += cost

    def ensure_within_budget(self) -> None:
        """Stop the run if it has crossed either ceiling.

        Raised after the fold, so the error carries what was actually
        billed — including the call that crossed the line.
        """
        from neosian._foundation.shared.exceptions import BudgetExceededError

        if (
            self.max_cost_micro_usd is not None
            and self._spent_micro_usd > self.max_cost_micro_usd
        ):
            raise BudgetExceededError(
                "cost",
                self.max_cost_micro_usd,
                self._spent_micro_usd,
                usage=self.usage,
                usage_by_model=self.usage_by_model,
            )
        if self.max_total_tokens is None:
            return
        total = self.usage
        if total is not None and total.total_tokens > self.max_total_tokens:
            raise BudgetExceededError(
                "tokens",
                self.max_total_tokens,
                total.total_tokens,
                usage=total,
                usage_by_model=self.usage_by_model,
            )

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
    sticky fallback is session behavior. `scope` is what this run may
    call and how it must answer, resolved once at the funnel (#224).
    """

    agent: Agent
    acquire: ClientFactory
    hooks: HookRunner
    fallback_state: FallbackState | None = None
    started: float = field(default_factory=time.monotonic)  # TurnEvent duration
    ledger: UsageLedger = field(default_factory=UsageLedger)
    scope: ToolScope = field(default_factory=ToolScope)


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
        """Fold one completion's usage into the run's ledger, then stop the
        run if that crossed its budget.

        Keyed by the API-reported model string; falls back to the
        requested model when the API reported none. This is the one
        attempt-facing fold, so it is where the ceiling is enforced
        (ledger #222).
        """
        self.ledger.record(api_model or self.model.value, usage, self.model)
        self.ledger.ensure_within_budget()

    def fold(self, api_model: str | None, usage: Usage) -> None:
        """Fold without enforcing — for the mid-turn remainder recorded
        inside an exception handler, where raising would mask the error
        actually escaping."""
        self.ledger.record(api_model or self.model.value, usage, self.model)

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
