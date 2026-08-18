"""Policy builder for GPT-OSS-Safeguard custom policies.

Provides structured policy creation with predefined and custom categories.
"""

import random
from dataclasses import dataclass, field

from neosian._foundation.shared.constants import Guardrails
from neosian._foundation.shared.prompt_assets import (
    POLICY_DATA,
    get_prompt,
    render,
)


@dataclass
class PolicyCategory:
    """A policy category with definition and examples.

    Attributes:
        name: Human-readable name (e.g., "Competitor Mentions").
        code: Short code for machine parsing (e.g., "P1").
        description: What this policy covers.
        violates: Example content that violates this policy.
        safe: Example content that does not violate this policy.
    """

    name: str
    code: str
    description: str
    violates: list[str] = field(default_factory=list)
    safe: list[str] = field(default_factory=list)

    def to_prompt(self) -> str:
        """Convert category to prompt format for GPT-OSS-Safeguard."""
        violates_str = (
            ", ".join(f'"{v}"' for v in self.violates) if self.violates else "N/A"
        )
        safe_str = ", ".join(f'"{s}"' for s in self.safe) if self.safe else "N/A"

        return render(
            get_prompt("guardrails.category"),
            code=self.code,
            name=self.name,
            description=self.description,
            violates=violates_str,
            safe=safe_str,
        )


class PolicyBuilder:
    """Builder for creating structured policies for GPT-OSS-Safeguard.

    Example:
        policy = (
            PolicyBuilder()
            .add(CommonPolicies.PROMPT_INJECTION)
            .add(CommonPolicies.COMPETITOR_MENTIONS)
            .add_custom(
                name="Internal Pricing",
                code="P10",
                description="Requests for internal pricing data",
                violates=["What's the enterprise price?"],
                safe=["Do you have pricing tiers?"],
            )
            .build()
        )
    """

    def __init__(self) -> None:
        """Initialize empty policy builder."""
        self._categories: list[PolicyCategory] = []

    def add(self, category: PolicyCategory) -> "PolicyBuilder":
        """Add a predefined policy category.

        Args:
            category: PolicyCategory to add.

        Returns:
            Self for method chaining.
        """
        self._categories.append(category)
        return self

    def add_custom(
        self,
        name: str,
        code: str,
        description: str,
        violates: list[str] | None = None,
        safe: list[str] | None = None,
    ) -> "PolicyBuilder":
        """Add a custom policy category.

        Args:
            name: Human-readable name.
            code: Short code (e.g., "P10").
            description: What this policy covers.
            violates: Example violations.
            safe: Example safe content.

        Returns:
            Self for method chaining.
        """
        self._categories.append(
            PolicyCategory(
                name=name,
                code=code,
                description=description,
                violates=violates or [],
                safe=safe or [],
            )
        )
        return self

    def build(self) -> str:
        """Build the policy prompt string.

        Returns:
            Formatted policy prompt for GPT-OSS-Safeguard.
        """
        if not self._categories:
            return ""

        policies_text = "\n".join(cat.to_prompt() for cat in self._categories)
        return policies_text

    @property
    def codes(self) -> list[str]:
        """List of all policy codes in this builder."""
        return [cat.code for cat in self._categories]

    @classmethod
    def test(cls) -> str:
        """Create a test policy that flags ~50% of content randomly.

        Useful for testing guardrail behavior without real policy evaluation.

        Returns:
            Special test policy string.
        """
        return Guardrails.TestPolicy.MARKER

    @classmethod
    def default(cls) -> str:
        """Create a default soft policy with common-sense rules.

        Returns:
            Default policy prompt string.
        """
        builder = cls()
        builder.add(CommonPolicies.PROMPT_INJECTION)
        builder.add(CommonPolicies.HARMFUL_INSTRUCTIONS)
        builder.add(CommonPolicies.PERSONAL_DATA_EXTRACTION)
        return builder.build()


def _shipped_policies() -> dict[str, PolicyCategory]:
    """The policy pack from assets/prompts/guardrails.yaml, keyed by code."""
    return {
        str(entry["code"]): PolicyCategory(
            name=str(entry["name"]),
            code=str(entry["code"]),
            description=str(entry["description"]),
            violates=[str(v) for v in entry["violates"]],
            safe=[str(s) for s in entry["safe"]],
        )
        for entry in POLICY_DATA
    }


_SHIPPED = _shipped_policies()


class CommonPolicies:
    """Predefined common policy categories.

    Content ships as data in assets/prompts/guardrails.yaml (ECOSYSTEM §8);
    override by building your own PolicyCategory list instead.
    """

    PROMPT_INJECTION = _SHIPPED["P1"]
    COMPETITOR_MENTIONS = _SHIPPED["P2"]
    INTERNAL_DATA = _SHIPPED["P3"]
    HARMFUL_INSTRUCTIONS = _SHIPPED["P4"]
    PERSONAL_DATA_EXTRACTION = _SHIPPED["P5"]
    PROFANITY_AND_ABUSE = _SHIPPED["P6"]


def is_test_policy(policy: str) -> bool:
    """Check if a policy is the special test policy.

    Args:
        policy: Policy string to check.

    Returns:
        True if this is the test policy.
    """
    return policy == Guardrails.TestPolicy.MARKER


def evaluate_test_policy() -> tuple[bool, str | None, str | None]:
    """Evaluate the test policy (50% random flag).

    Returns:
        Tuple of (safe, category, rationale).
    """
    if random.random() < 0.5:
        return (
            False,
            Guardrails.TestPolicy.CATEGORY,
            Guardrails.TestPolicy.RATIONALE,
        )
    return (True, None, None)
