"""Fallback policy: which models a run tries, in what order, and what
happens between tries (DESIGN §3).

Pure decisions over the run's context — the two drivers (`blocking`,
`stream_run`) own the transport and differ in nothing else. Sticky
fallback, the retry-main return, capability routing and the exhaustion
shapes all live here, once; a multi-leg ladder is a change to `legs`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, NoReturn

from neosian._foundation.agent.fallback import (
    ensure_fallback_viable,
    reraise_caller_errors,
    unsupported_content_types,
)
from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.exceptions import (
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
    """One model to try."""

    model: AnyModel
    outcome: LegOutcome


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
    fallback = agent._fallback_model
    main = Leg(agent._model, LegOutcome.MAIN_OK)
    preamble: Switch | None = None
    if state is not None and state.using_fallback and agent._should_retry_main(state):
        assert fallback is not None  # _should_retry_main checked
        logger.info(
            ErrorMessages.FALLBACK_RETRY_MAIN.format(
                main_model=agent._model.value,
                successful_calls=state.successful_fallback_calls,
            )
        )
        state.using_fallback = False
        state.successful_fallback_calls = 0
        preamble = Switch(
            from_model=fallback.value,
            to_model=agent._model.value,
            reason="retry_main_after threshold reached",
            cause=None,
            sticky=True,
        )
    if state is not None and state.using_fallback:
        if fallback is None:
            raise ModelFailedError(
                model=agent._model.value,
                error="No fallback configured but fallback_state.using_fallback=True",
                has_fallback=False,
            )
        if unsupported_content_types(fallback, messages):
            return Plan(legs=(main,))
        return Plan(legs=(Leg(fallback, LegOutcome.FALLBACK_AGAIN), main))
    if fallback is None:
        return Plan(legs=(main,), preamble=preamble)
    return Plan(legs=(main, Leg(fallback, LegOutcome.FALLBACK_OK)), preamble=preamble)


def record_success(leg: Leg, state: FallbackState | None) -> None:
    """Apply a successful leg to the session's sticky state."""
    if state is None:
        return
    if leg.outcome is LegOutcome.FALLBACK_AGAIN:
        state.successful_fallback_calls += 1
        return
    state.using_fallback = leg.outcome is LegOutcome.FALLBACK_OK
    state.successful_fallback_calls = int(state.using_fallback)


def switch_after(
    agent: Agent,
    failed: Leg,
    next_leg: Leg,
    error: Exception,
    messages: list[Message],
    attempt: Attempt,
) -> Switch:
    """The switch a failed leg leads to.

    The main→fallback switch is capability-gated (`ensure_fallback_viable`
    raises instead of downgrading media or shrinking a window); the sticky
    fallback→main last resort is not — the main model was chosen for this
    conversation.
    """
    if next_leg.outcome is LegOutcome.FALLBACK_OK:
        ensure_fallback_viable(agent, error, messages, attempt=attempt)
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

    One leg: a caller-input error (unsupported content, context overflow)
    re-raises as itself, anything else as `ModelFailedError`. Two legs:
    `FallbackExhaustedError` with each model's own error, chained from
    the last failure.
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
    assert agent._fallback_model is not None  # two legs imply a fallback
    errors = {leg.outcome is LegOutcome.MAIN_OK: str(e) for leg, e in failures}
    raise FallbackExhaustedError(
        main_model=agent._model.value,
        main_error=errors[True],
        fallback_model=agent._fallback_model.value,
        fallback_error=errors[False],
        usage=attempt.usage,
        usage_by_model=attempt.usage_by_model,
    ) from last
