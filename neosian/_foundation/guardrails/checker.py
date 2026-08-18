"""Policy checker module for GPT-OSS-Safeguard policy enforcement.

Handles parsing and evaluation of GPT-OSS-Safeguard responses.
"""

import json

from groq import AsyncGroq

from neosian._foundation.guardrails.policy import evaluate_test_policy, is_test_policy
from neosian._foundation.shared.constants import Guardrails
from neosian._foundation.shared.exceptions import GuardrailPolicyParseError
from neosian._foundation.shared.prompt_assets import get_prompt, render
from neosian._foundation.shared.types import PolicyResult


def parse_policy_response(response: str) -> PolicyResult:
    """Parse GPT-OSS-Safeguard JSON response into PolicyResult.

    Expected format:
    {"violation": 0|1, "category": "P1"|null, "rationale": "..."}

    Args:
        response: Raw JSON response from GPT-OSS-Safeguard.

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
    client: AsyncGroq,
    model: str | None = None,
) -> PolicyResult:
    """Check content against a custom policy using GPT-OSS-Safeguard.

    Args:
        content: Content to evaluate.
        policy: Policy prompt (from PolicyBuilder.build()).
        client: Groq async client.
        model: Model to use (defaults to Guardrails.POLICY_MODEL).

    Returns:
        PolicyResult with evaluation result.

    Raises:
        GuardrailPolicyParseError: If response cannot be parsed.
    """
    # Handle test policy
    if is_test_policy(policy):
        safe, category, rationale = evaluate_test_policy()
        return PolicyResult(safe=safe, category=category, rationale=rationale)

    model = model or Guardrails.POLICY_MODEL

    # Build the full prompt with policy and content
    full_prompt = render(
        get_prompt("guardrails.classifier"),
        policies=policy,
        content=content,
    )

    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": full_prompt}],
        temperature=Guardrails.TEMPERATURE,
    )

    response_text = response.choices[0].message.content or ""
    return parse_policy_response(response_text)
