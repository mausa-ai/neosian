"""Type definitions for neosian.

All NewType definitions are centralized here.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, NewType

from neosian._foundation.shared.constants import ErrorMessages

if TYPE_CHECKING:
    from neosian._foundation.tools.base import ToolResult

# Core identifiers
AgentName = NewType("AgentName", str)
ToolName = NewType("ToolName", str)
ModelId = NewType("ModelId", str)

# Content types
SystemPrompt = NewType("SystemPrompt", str)
UserMessage = NewType("UserMessage", str)
AssistantMessage = NewType("AssistantMessage", str)
ToolCallId = NewType("ToolCallId", str)

# Provider identifiers
ProviderId = NewType("ProviderId", str)

# Tool function type (defined here to avoid circular imports)
ToolFunction = Callable[..., Awaitable["ToolResult[Any]"]]


# Todo status enum
class TodoStatus(str, Enum):
    """Status of a todo item."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass
class AgentConfig:
    """Configuration for an agent.

    This is the contract for one-file agent definitions.
    Export a `configuration` variable of this type.

    Example:
        from neosian import AgentConfig, Tool, ToolResult

        @Tool(name="greet", description="Say hello")
        async def greet(name: str) -> ToolResult[str]:
            return ToolResult.ok(f"Hello {name}")

        configuration = AgentConfig(
            system_prompt="You are helpful.",
            tools=[greet],
        )
    """

    system_prompt: SystemPrompt
    tools: list[ToolFunction] = field(default_factory=list)
    provider: ProviderId | None = None
    model: ModelId | None = None
    enable_todo: bool = True
    guardrails: "GuardrailsConfig | None" = None


# Guardrail types
class GuardrailMode(str, Enum):
    """Guardrail execution mode.

    Defines how classifier and policy guardrails interact:
    - NONE: No guardrails
    - CLASSIFIER_ONLY: Run Llama Guard classifier only
    - POLICY_ONLY: Run custom policy only
    - CLASSIFIER_AND_POLICY: Run both, always
    - CLASSIFIER_THEN_POLICY: Run policy only if classifier flags (optimization)
    """

    NONE = "none"
    CLASSIFIER_ONLY = "classifier_only"
    POLICY_ONLY = "policy_only"
    CLASSIFIER_AND_POLICY = "classifier_and_policy"
    CLASSIFIER_THEN_POLICY = "classifier_then_policy"

    def uses_classifier(self) -> bool:
        """Check if this mode uses the classifier."""
        return self in (
            GuardrailMode.CLASSIFIER_ONLY,
            GuardrailMode.CLASSIFIER_AND_POLICY,
            GuardrailMode.CLASSIFIER_THEN_POLICY,
        )

    def uses_policy(self) -> bool:
        """Check if this mode uses policy."""
        return self in (
            GuardrailMode.POLICY_ONLY,
            GuardrailMode.CLASSIFIER_AND_POLICY,
            GuardrailMode.CLASSIFIER_THEN_POLICY,
        )


@dataclass
class GuardrailsConfig:
    """Configuration for input and output guardrails.

    Two guardrail types:
    - Classifier (Llama Guard 4): Fast, fixed taxonomy (S1-S14)
    - Policy (GPT-OSS-Safeguard): Custom rules, flexible

    Modes:
    - NONE: No guardrails
    - CLASSIFIER_ONLY: Run Llama Guard only
    - POLICY_ONLY: Run custom policy only (requires policy string)
    - CLASSIFIER_AND_POLICY: Run both, always
    - CLASSIFIER_THEN_POLICY: Run policy only if classifier flags (optimization)

    Note: Output guardrails only work with stream=False.

    Example:
        from neosian import GuardrailsConfig, GuardrailMode, PolicyBuilder

        # Classifier only
        guardrails = GuardrailsConfig(
            input_mode=GuardrailMode.CLASSIFIER_ONLY,
        )

        # Policy only
        guardrails = GuardrailsConfig(
            input_mode=GuardrailMode.POLICY_ONLY,
            input_policy=PolicyBuilder.default(),
        )

        # Classifier as pre-filter, policy for detailed check
        guardrails = GuardrailsConfig(
            input_mode=GuardrailMode.CLASSIFIER_THEN_POLICY,
            input_policy=PolicyBuilder.default(),
            block_on_input=True,
        )
    """

    # Input guardrails
    input_mode: GuardrailMode = GuardrailMode.NONE
    input_policy: str | None = None
    block_on_input: bool = True

    # Output guardrails (only work with stream=False)
    output_mode: GuardrailMode = GuardrailMode.NONE
    output_policy: str | None = None

    def __post_init__(self) -> None:
        """Validate configuration."""
        # Check input policy requirement
        if self.input_mode.uses_policy() and self.input_policy is None:
            raise ValueError(
                ErrorMessages.GUARDRAIL_POLICY_REQUIRED.format(
                    mode=f"input_mode={self.input_mode.value}",
                    policy_field="input_policy",
                )
            )

        # Check output policy requirement
        if self.output_mode.uses_policy() and self.output_policy is None:
            raise ValueError(
                ErrorMessages.GUARDRAIL_POLICY_REQUIRED.format(
                    mode=f"output_mode={self.output_mode.value}",
                    policy_field="output_policy",
                )
            )

    @property
    def has_output_guardrails(self) -> bool:
        """Check if any output guardrails are configured."""
        return self.output_mode != GuardrailMode.NONE


@dataclass
class ClassifierResult:
    """Result from Llama Guard classification.

    Attributes:
        safe: Whether the content passed classification.
        categories: List of flagged category codes (e.g., ["S2", "S5"]).
    """

    safe: bool
    categories: list[str] = field(default_factory=list)


@dataclass
class PolicyResult:
    """Result from GPT-OSS-Safeguard policy check.

    Attributes:
        safe: Whether the content passed the policy check.
        category: The policy code that was violated (e.g., "P1").
        rationale: Explanation of why content was flagged.
    """

    safe: bool
    category: str | None = None
    rationale: str | None = None


@dataclass
class GuardrailResult:
    """Combined result from all guardrail checks.

    Attributes:
        safe: Overall safety status.
        blocked_at: Where the content was blocked ("input" or "output").
        input_classifier: Llama Guard result for input.
        input_policy: Policy result for input.
        output_classifier: Llama Guard result for output.
        output_policy: Policy result for output.
    """

    safe: bool
    blocked_at: Literal["input", "output"] | None = None
    input_classifier: ClassifierResult | None = None
    input_policy: PolicyResult | None = None
    output_classifier: ClassifierResult | None = None
    output_policy: PolicyResult | None = None

    @property
    def flagged_categories(self) -> list[str]:
        """All flagged Llama Guard categories from both input and output."""
        categories: list[str] = []
        if self.input_classifier:
            categories.extend(self.input_classifier.categories)
        if self.output_classifier:
            categories.extend(self.output_classifier.categories)
        return categories

    @property
    def policy_rationale(self) -> str | None:
        """First available policy rationale (input or output)."""
        if self.input_policy and self.input_policy.rationale:
            return self.input_policy.rationale
        if self.output_policy and self.output_policy.rationale:
            return self.output_policy.rationale
        return None
