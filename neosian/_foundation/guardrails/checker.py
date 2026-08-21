"""Policy checker module for guardrail policy enforcement.

Handles parsing and evaluation of policy-classifier responses. The
classifier prompt (assets/prompts/guardrails.yaml) runs through a
neosian client on a configurable model — `GuardrailsConfig.model`,
defaulting to the agent's own configured model (ledger #84) — so
guardrails carry no hidden provider dependency and run keylessly on
FakeProvider.
"""

import json

from neosian._foundation.guardrails.policy import evaluate_test_policy, is_test_policy
from neosian._foundation.llm.base import BaseLLMClient, Message, Role, text_of
from neosian._foundation.shared.exceptions import GuardrailPolicyParseError
from neosian._foundation.shared.prompt_assets import get_prompt, render
from neosian._foundation.shared.types import Model, PolicyResult


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


async def check_with_policy(
    content: str,
    policy: str,
    client: BaseLLMClient,
    model: Model,
) -> PolicyResult:
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
        PolicyResult with evaluation result.

    Raises:
        GuardrailPolicyParseError: If response cannot be parsed.
    """
    # Handle test policy
    if is_test_policy(policy):
        safe, category, rationale = evaluate_test_policy()
        return PolicyResult(safe=safe, category=category, rationale=rationale)

    # Build the full prompt with policy and content
    full_prompt = render(
        get_prompt("guardrails.classifier"),
        policies=policy,
        content=content,
    )

    response = await client.complete(
        messages=[Message(role=Role.USER, content=full_prompt)],
        model=model,
    )
    return parse_policy_response(text_of(response.message))
