"""Fallback policy: which models a run tries, in what order, and what
happens between tries (DESIGN §3).

Pure decisions over the run's context — the two drivers (`blocking`,
`stream_run`) own the transport and differ in nothing else. Sticky
fallback, the retry-main return, capability routing and the exhaustion
shapes all live here, once; a multi-leg ladder is a change to `legs`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING, NoReturn

from neosian._foundation.agent.fallback import (
    ensure_fallback_viable,
    reraise_caller_errors,
    unsupported_content_types,
)
from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.exceptions import (
    BudgetExceededError,
    FallbackExhaustedError,
    ModelFailedError,
)
from neosian._foundation.shared.types import AnyModel, FallbackState

if TYPE_CHECKING:
    from neosian._foundation.agent.base import Agent
    from neosian._foundation.agent.context import Attempt, RunContext
    from neosian._foundation.llm.base import Message

logger = logging.getLogger(__name__)


class LegOutcome(str, Enum):
    """What a leg's success means for the session's sticky state."""

    MAIN_OK = "main_ok"  # the main model answered: sticky state resets
    FALLBACK_OK = "fallback_ok"  # the fallback answered after main failed
    FALLBACK_AGAIN = "fallback_again"  # a sticky fallback answered once more


@dataclass(frozen=True, slots=True)
class Leg:
    """One model to try.

    `index` is the rung it came from: 0 is the main model, 1..n the
    fallback ladder in order (NC9, ledger #223).
    """

    model: AnyModel
    outcome: LegOutcome
    index: int = 0


@dataclass(frozen=True, slots=True)
class Switch:
    """One model switch — the `on_fallback` event minus the transport."""

    from_model: str
    to_model: str
    reason: str
    cause: BaseException | None
    sticky: bool


@dataclass(frozen=True, slots=True)
class Plan:
    """The legs to try in order; `preamble` is the sticky retry-main
    return, emitted before the first leg."""

    legs: tuple[Leg, ...]
    preamble: Switch | None = None


def build_plan(ctx: RunContext, messages: list[Message]) -> Plan:
    """Decide the run's legs from the agent's fallback and the session's
    sticky state.

    Mutates `ctx.fallback_state` for the retry-main return (the state
    flips before the main attempt, as it always did). A sticky session
    whose fallback model cannot carry the conversation's media routes
    straight to the main model, alone.
    """
    agent = ctx.agent
    state = ctx.fallback_state
    ladder = agent._fallback_models
    main = Leg(agent._model, LegOutcome.MAIN_OK, index=0)
    preamble: Switch | None = None
    if state is not None and state.using_fallback and agent._should_retry_main(state):
        assert ladder  # _should_retry_main checked a fallback exists
        preamble = Switch(
            from_model=_rung(ladder, state.fallback_index).value,
            to_model=agent._model.value,
            reason="retry_main_after threshold reached",
            cause=None,
            sticky=True,
        )
        logger.info(
            ErrorMessages.FALLBACK_RETRY_MAIN.format(
                main_model=agent._model.value,
                successful_calls=state.successful_fallback_calls,
            )
        )
        state.using_fallback = False
        state.successful_fallback_calls = 0
    if state is not None and state.using_fallback:
        if not ladder:
            raise ModelFailedError(
                model=agent._model.value,
                error="No fallback configured but fallback_state.using_fallback=True",
                has_fallback=False,
            )
        # The sticky rung first, then the rungs below it, then the main
        # model as the last resort — a sticky rung that fails keeps
        # walking down before giving the main model another try.
        start = min(state.fallback_index, len(ladder) - 1)
        rungs = _viable(ladder[start:], messages, offset=start + 1)
        # The sticky rung answering again is a different outcome from a
        # lower rung taking over: the first increments the run of
        # successes, the second starts a new one.
        if rungs and rungs[0].index == start + 1:
            rungs = (replace(rungs[0], outcome=LegOutcome.FALLBACK_AGAIN), *rungs[1:])
        return Plan(legs=(*rungs, main))
    if not ladder:
        return Plan(legs=(main,), preamble=preamble)
    rungs = _viable(ladder, messages, offset=1)
    return Plan(legs=(main, *rungs), preamble=preamble)


def _rung(ladder: tuple[AnyModel, ...], index: int) -> AnyModel:
    """The ladder rung at `index`, clamped — a shortened ladder under a
    session whose sticky index outlived it reads as its last rung."""
    return ladder[min(index, len(ladder) - 1)]


def _viable(
    ladder: tuple[AnyModel, ...], messages: list[Message], *, offset: int
) -> tuple[Leg, ...]:
    """The rungs that can carry this conversation's content, as legs.

    Media is never downgraded onto a model that cannot handle it, so a
    rung that cannot is dropped from the ladder rather than attempted —
    the same gate `ensure_fallback_viable` applies per hop, moved up to
    where the whole ladder is known (DESIGN §2).
    """
    return tuple(
        Leg(model, LegOutcome.FALLBACK_OK, index=offset + position)
        for position, model in enumerate(ladder)
        if not unsupported_content_types(model, messages)
    )


def record_success(leg: Leg, state: FallbackState | None) -> None:
    """Apply a successful leg to the session's sticky state.

    `fallback_index` follows the rung that answered, so the next run
    starts where this one ended up rather than at the top of the ladder.
    """
    if state is None:
        return
    if leg.outcome is LegOutcome.FALLBACK_AGAIN:
        state.successful_fallback_calls += 1
        return
    state.using_fallback = leg.outcome is LegOutcome.FALLBACK_OK
    state.successful_fallback_calls = int(state.using_fallback)
    # Leg indices are 1-based over the ladder; the main model resets to 0.
    state.fallback_index = max(leg.index - 1, 0)


def switch_after(
    agent: Agent,
    failed: Leg,
    next_leg: Leg,
    error: Exception,
    messages: list[Message],
    attempt: Attempt,
) -> Switch:
    """The switch a failed leg leads to.

    A switch onto a ladder rung is capability-gated
    (`ensure_fallback_viable` raises instead of downgrading media or
    shrinking a window); the fallback→main last resort is not — the main
    model was chosen for this conversation.
    """
    # A crossed budget is the run's ceiling, not this model's failure:
    # switching legs would bill past the cap the caller set (#222).
    if isinstance(error, BudgetExceededError):
        raise error
    if next_leg.index > 0:
        ensure_fallback_viable(
            agent, error, messages, attempt=attempt, target=next_leg.model
        )
    logger.warning(
        ErrorMessages.FALLBACK_TRIGGERED.format(
            from_model=failed.model.value,
            to_model=next_leg.model.value,
            reason=str(error),
        )
    )
    return Switch(
        from_model=failed.model.value,
        to_model=next_leg.model.value,
        reason=str(error),
        cause=error,
        sticky=False,
    )


def give_up(
    agent: Agent, failures: list[tuple[Leg, Exception]], attempt: Attempt
) -> NoReturn:
    """Every leg failed: raise the run's terminal error with its billed usage.

    One leg: a caller-input error (unsupported content, context overflow,
    a crossed budget) re-raises as itself, anything else as
    `ModelFailedError`. Two or more: `FallbackExhaustedError` carrying
    every attempt in the order they were made, chained from the last
    failure.
    """
    last = failures[-1][1]
    if len(failures) == 1:
        reraise_caller_errors(last, attempt)
        raise ModelFailedError(
            model=agent._model.value,
            error=str(last),
            has_fallback=False,
            usage=attempt.usage,
            usage_by_model=attempt.usage_by_model,
        ) from last
    attempts = tuple((leg.model.value, str(error)) for leg, error in failures)
    # `main_*`/`fallback_*` keep their original meaning — the *main
    # model's* failure and a *fallback rung's*, never "first and last
    # tried": a sticky run walks the ladder before the main model, and
    # hosts key on which model it was. `attempts` carries trial order
    # (NC9, ledger #223).
    main = next((f for f in failures if f[0].index == 0), failures[0])
    rungs = [f for f in failures if f[0].index > 0]
    last_rung = rungs[-1] if rungs else failures[-1]
    raise FallbackExhaustedError(
        main_model=main[0].model.value,
        main_error=str(main[1]),
        fallback_model=last_rung[0].model.value,
        fallback_error=str(last_rung[1]),
        attempts=attempts,
        usage=attempt.usage,
        usage_by_model=attempt.usage_by_model,
    ) from last
