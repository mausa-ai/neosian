"""Policy checker module for guardrail policy enforcement.

Handles parsing and evaluation of policy-classifier responses. The
classifier prompt (assets/prompts/guardrails.yaml) runs through a
neosian client on a configurable model — `GuardrailsConfig.model`,
defaulting to the agent's own configured model (ledger #84) — so
guardrails carry no hidden provider dependency and run keylessly on
FakeProvider.
"""

import json
import secrets
from dataclasses import dataclass

from pydantic import BaseModel

from neosian._foundation.guardrails.policy import evaluate_test_policy, is_test_policy
from neosian._foundation.llm.base import BaseLLMClient, Message, Role, Usage, text_of
from neosian._foundation.shared.exceptions import GuardrailPolicyParseError
from neosian._foundation.shared.prompt_assets import get_prompt, render
from neosian._foundation.shared.types import AnyModel, PolicyResult, ResponseFormat


class _PolicyVerdict(BaseModel):
    """The verdict's wire schema — the provider-side constraint (TG-6).

    Every field is required (OpenAI strict mode's rule; nullable via the
    type). `parse_policy_response` stays the parser: its rules are
    stricter than a lax model (a string violation is rejected, a missing
    one reads unsafe), and they are pinned.
    """

    violation: int
    category: str | None
    rationale: str | None


def parse_policy_response(response: str) -> PolicyResult:
    """Parse the classifier's JSON response into PolicyResult.

    Expected format:
    {"violation": 0|1, "category": "P1"|null, "rationale": "..."}

    Args:
        response: Raw JSON response from the policy classifier.

    Returns:
        PolicyResult with safe status, category, and rationale.

    Raises:
        GuardrailPolicyParseError: If response cannot be parsed or has invalid format.
    """
    try:
        data = json.loads(response.strip())

        # Validate response is a dict (not array or primitive)
        if not isinstance(data, dict):
            raise GuardrailPolicyParseError(response)

        # Get violation field with validation
        violation = data.get("violation", 1)
        if not isinstance(violation, int):
            raise GuardrailPolicyParseError(response)

        is_safe = violation == 0

        # Only include category and rationale when flagged (not safe)
        # Rationale for safe content is meaningless noise
        return PolicyResult(
            safe=is_safe,
            category=data.get("category") if not is_safe else None,
            rationale=data.get("rationale") if not is_safe else None,
        )
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise GuardrailPolicyParseError(response) from e


@dataclass(frozen=True, slots=True)
class GuardOutcome:
    """One classifier verdict with what it cost: the call is billed, so
    its usage and API-reported model travel to the run's ledger (TG-4).
    The test policy answers for free."""

    policy: PolicyResult
    usage: Usage | None = None
    api_model: str | None = None


async def check_with_policy(
    content: str,
    policy: str,
    client: BaseLLMClient,
    model: AnyModel,
) -> GuardOutcome:
    """Check content against a custom policy via the classifier prompt.

    No explicit temperature is sent — provider defaults are the only
    portable choice (some models reject a non-default temperature), and
    the classifier's JSON contract does not depend on it.

    Args:
        content: Content to evaluate.
        policy: Policy prompt (from PolicyBuilder.build()).
        client: The guardrail model's neosian client.
        model: The policy model (GuardrailsConfig.model or the agent's).

    Returns:
        The verdict and its spend.

    Raises:
        GuardrailPolicyParseError: If response cannot be parsed.
    """
    # Handle test policy
    if is_test_policy(policy):
        safe, category, rationale = evaluate_test_policy()
        return GuardOutcome(
            PolicyResult(safe=safe, category=category, rationale=rationale)
        )

    # Build the full prompt with policy and content. The untrusted content
    # is fenced behind a per-call nonce (TG-5); the renderer substitutes in
    # keyword order, so the nonce lands first and the content last — a
    # literal "{{nonce}}" inside the content can never expand.
    full_prompt = render(
        get_prompt("guardrails.classifier"),
        nonce=secrets.token_hex(8),
        policies=policy,
        content=content,
    )

    response = await client.complete(
        messages=[Message(role=Role.USER, content=full_prompt)],
        model=model,
        response_format=ResponseFormat(schema=_PolicyVerdict),
    )
    try:
        policy_result = parse_policy_response(text_of(response.message))
    except GuardrailPolicyParseError as exc:
        # The call was billed whether or not the verdict parsed.
        raise GuardrailPolicyParseError(
            exc.response, usage=response.usage, api_model=response.model
        ) from exc
    return GuardOutcome(policy_result, usage=response.usage, api_model=response.model)
