"""Guardrail plumbing shared by the blocking and streaming paths."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
from typing import TYPE_CHECKING, Literal

from neosian._foundation.agent.emit import blocked_response, emit_turn
from neosian._foundation.agent.events import BlockedEvent
from neosian._foundation.agent.response import AgentResponse
from neosian._foundation.guardrails.checker import check_with_policy
from neosian._foundation.llm.base import Message, ModelUsage, Role, Usage, text_of
from neosian._foundation.shared.constants import EnvVars
from neosian._foundation.shared.exceptions import (
    GuardrailPolicyParseError,
    MissingAPIKeyError,
)
from neosian._foundation.shared.types import (
    AnyModel,
    GuardrailErrorPolicy,
    GuardrailMode,
    GuardrailResult,
    PolicyResult,
    Provider,
)

if TYPE_CHECKING:
    from neosian._foundation.agent.base import Agent
    from neosian._foundation.agent.context import RunContext, UsageLedger

logger = logging.getLogger(__name__)

_PROVIDER_ENV: dict[Provider, str] = {
    Provider.OPENAI: EnvVars.OPENAI_API_KEY,
    Provider.ANTHROPIC: EnvVars.ANTHROPIC_API_KEY,
    Provider.CEREBRAS: EnvVars.CEREBRAS_API_KEY,
}


def require_provider_key(provider: Provider) -> None:
    """Raise loudly at construction when the guardrail model's provider
    has no key — a guardrail that silently fail-opens because its key
    is absent would be a security footgun, so absence is an error the
    moment guardrails are configured, never at check time.

    FAKE needs no key; a caller-supplied client_factory bypasses this
    (the factory owns credentials).
    """
    _require_env(_PROVIDER_ENV.get(provider))


def require_model_key(model: AnyModel) -> None:
    """The model-keyed form: a door row's door names its own env var
    (DESIGN §19); adapter rows defer to `require_provider_key`."""
    if model.door is not None:
        _require_env(model.door.api_key_env)
    else:
        require_provider_key(model.provider)


def _require_env(env_var: str | None) -> None:
    if env_var is not None and not os.environ.get(env_var):
        raise MissingAPIKeyError(f"{env_var} environment variable not set")


async def check_guardrails(
    ctx: RunContext,
    content: str,
    checkpoint: Literal["input", "output"],
) -> tuple[bool, PolicyResult | None]:
    """Check content against configured guardrails at the given checkpoint.

    The classifier's client comes from the run's acquire seam — a
    session's pool owns and closes it (AG-6); an Agent holds none.

    Args:
        ctx: Per-run context (client acquisition).
        content: Content to check.
        checkpoint: "input" or "output" checkpoint.

    Returns:
        Tuple of (is_safe, policy_result).
    """
    agent = ctx.agent
    if agent._guardrails is None or agent._guardrail_model is None:
        return (True, None)

    # Select config fields based on checkpoint
    mode = (
        agent._guardrails.input_mode
        if checkpoint == "input"
        else agent._guardrails.output_mode
    )
    policy = (
        agent._guardrails.input_policy
        if checkpoint == "input"
        else agent._guardrails.output_policy
    )

    # No guardrails configured for this checkpoint
    if mode == GuardrailMode.NONE:
        return (True, None)

    # Run policy check, bounded by the config's deadline: a hung
    # classifier is an error under `error_policy`, never a hung run.
    if policy is not None:
        try:
            outcome = await asyncio.wait_for(
                check_with_policy(
                    content=content,
                    policy=policy,
                    client=ctx.acquire(agent._guardrail_model),
                    model=agent._guardrail_model,
                ),
                timeout=agent._guardrails.timeout_seconds,
            )
        except GuardrailPolicyParseError as exc:
            # Unparseable, yet billed: the ledger records it before the
            # error policy decides (never undercount).
            if exc.usage is not None:
                ctx.ledger.record(
                    exc.api_model or agent._guardrail_model.value, exc.usage
                )
            raise
        # The classifier's call is billed: it lands on the run's ledger
        # under its own API-reported model, so every terminal value the
        # run produces carries it — never undercount (TG-4).
        if outcome.usage is not None:
            ctx.ledger.record(
                outcome.api_model or agent._guardrail_model.value, outcome.usage
            )
        return (outcome.policy.safe, outcome.policy)

    # Fallback (shouldn't reach here with valid config)
    return (True, None)


def extract_user_content(messages: list[Message]) -> str:
    """Extract user content from messages for guardrail checking.

    Returns the text of the last user message in the conversation — the
    message that decides; a text-less last message (media-only, empty)
    yields "" with a warning rather than an older message's text. This
    assumes the app appends the new user input as the last message
    before calling agent.run().

    Expected app pattern:
        messages = db.load_history(user_id)  # Previous messages
        messages.append(Message(role=Role.USER, content=new_input))
        response = await agent.run(messages, stream=False)

    Args:
        messages: Conversation history.

    Returns:
        Content of the last user message, or empty string if none found.
    """
    for message in reversed(messages):
        if message.role != Role.USER:
            continue
        text = text_of(message)
        if not text:
            # The last user message decides; an older text message is never
            # classified in its place (TG-15) — say so, never skip silently.
            logger.warning(
                "Input guardrail skipped: the last user message has no text "
                "to classify (%s)",
                "media-only" if isinstance(message.content, list) else "empty",
            )
        return text
    return ""


def get_guard_result_safe(
    agent: Agent,
    guard_task: asyncio.Task[tuple[bool, PolicyResult | None]],
) -> tuple[bool, PolicyResult | None]:
    """Get guard task result with error policy handling.

    Handles exceptions from guard task based on configured error_policy:
    - FAIL_OPEN: On error, treat as safe (log warning)
    - FAIL_CLOSED: On error, treat as blocked (log error)

    Args:
        guard_task: Completed guard task.

    Returns:
        Tuple of (is_safe, policy_result).
    """
    try:
        return guard_task.result()
    except Exception as e:
        return handle_guard_error(agent, e)


async def await_guard_result_safe(
    agent: Agent,
    guard_task: asyncio.Task[tuple[bool, PolicyResult | None]],
) -> tuple[bool, PolicyResult | None]:
    """Await guard task result with error policy handling.

    Async version of _get_guard_result_safe for awaiting pending tasks.

    Args:
        guard_task: Guard task to await.

    Returns:
        Tuple of (is_safe, policy_result).
    """
    try:
        return await guard_task
    except Exception as e:
        return handle_guard_error(agent, e)


def handle_guard_error(
    agent: Agent, error: Exception
) -> tuple[bool, PolicyResult | None]:
    """Handle guardrail error based on error_policy.

    Args:
        error: The exception that occurred.

    Returns:
        Tuple of (is_safe, policy_result) based on error policy.
    """
    error_policy = (
        agent._guardrails.error_policy
        if agent._guardrails
        else GuardrailErrorPolicy.FAIL_OPEN
    )
    # A classifier that answered but could not be parsed is not "never
    # answered" (TG-6): it is named at ERROR, by size — the reply itself
    # is untrusted text and stays out of the log.
    unparseable = isinstance(error, GuardrailPolicyParseError)
    detail = (
        "the classifier answered but its verdict was unparseable "
        f"({len(error.response)} chars)"
        if isinstance(error, GuardrailPolicyParseError)
        else str(error)
    )

    if error_policy == GuardrailErrorPolicy.FAIL_OPEN:
        log = logger.error if unparseable else logger.warning
        log("Guardrail check failed (fail-open): %s. Treating as safe.", detail)
        return (True, None)
    logger.error(
        "Guardrail check failed (fail-closed): %s. Treating as blocked.", detail
    )
    return (False, None)


async def check_guard_and_block(
    ctx: RunContext,
    guard_task: asyncio.Task[tuple[bool, PolicyResult | None]],
    *,
    usage: Usage | None = None,
    usage_by_model: tuple[ModelUsage, ...] = (),
) -> BlockedEvent | None:
    """Check completed guard task and return a BlockedEvent if needed.

    Handles guard task errors based on error_policy configuration.
    A block is a streamed run's terminal, so on_turn fires here with
    the blocked value object.

    Args:
        ctx: Per-run context (hook dispatch).
        guard_task: Completed guard task.
        usage: Best-effort token usage billed before the block,
            carried on the blocked event.
        usage_by_model: Per-model split of `usage` for both events.

    Returns:
        Unstamped BlockedEvent if guard flagged and block_on_input=True,
        else None.
    """
    agent = ctx.agent
    is_safe, policy = get_guard_result_safe(agent, guard_task)

    if not is_safe and agent._guardrails and agent._guardrails.block_on_input:
        rationale = policy.rationale if policy else None
        await emit_turn(
            ctx,
            blocked_response(policy, usage, usage_by_model),
            streamed=True,
        )
        return BlockedEvent(
            rationale=rationale,
            usage=usage,
            usage_by_model=usage_by_model,
        )

    return None


def attach_input_guard_results(
    response: AgentResponse,
    input_policy: PolicyResult | None,
    ledger: UsageLedger,
) -> AgentResponse:
    """Attach input guardrail results to an existing AgentResponse.

    Used when agent finishes before guard in parallel execution — the
    response was finalized before the verdict landed, so usage is re-read
    off the run's ledger, which now carries the classifier's spend.

    Args:
        response: The agent response to augment.
        input_policy: Input policy result.
        ledger: The run's usage ledger.

    Returns:
        AgentResponse with updated guardrail_result and usage.
    """
    if input_policy is None:
        return response

    # Determine input safety
    is_input_safe = input_policy.safe

    # Merge with existing guardrail result (from output check)
    existing = response.guardrail_result
    if existing is not None:
        overall_safe = is_input_safe and existing.safe
        flagged_at = "input" if not is_input_safe else existing.flagged_at
        guardrail_result = GuardrailResult(
            safe=overall_safe,
            flagged_at=flagged_at,
            input_policy=input_policy,
            output_policy=existing.output_policy,
        )
    else:
        guardrail_result = GuardrailResult(
            safe=is_input_safe,
            flagged_at="input" if not is_input_safe else None,
            input_policy=input_policy,
        )

    # dataclasses.replace, never a field-by-field rebuild — the manual
    # version silently dropped model= (DESIGN §3 found-bug register #2).
    usage = ledger.usage
    return dataclasses.replace(
        response,
        guardrail_result=guardrail_result,
        usage=usage if usage is not None else Usage(input_tokens=0, output_tokens=0),
        usage_by_model=ledger.usage_by_model,
    )
