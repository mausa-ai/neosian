"""Guardrail configuration and verdict types (DESIGN §3; split from
`types.py` at NF, TG-36 — `types.py` re-exports every name)."""

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from neosian._foundation.shared.registry import AnyModel, resolve_model


class GuardrailMode(str, Enum):
    """Guardrail execution mode.

    - NONE: No guardrails
    - POLICY_ONLY: Run custom policy via GPT-OSS-Safeguard
    """

    NONE = "none"
    POLICY_ONLY = "policy_only"


class GuardrailErrorPolicy(str, Enum):
    """Policy for handling guardrail errors (API failures, timeouts, etc.).

    - FAIL_OPEN: On error, treat as safe and continue (default). Best for UX.
    - FAIL_CLOSED: On error, treat as blocked. Best for high-security apps.
    """

    FAIL_OPEN = "fail_open"
    FAIL_CLOSED = "fail_closed"


@dataclass
class GuardrailsConfig:
    """Configuration for input and output guardrails.

    Policy-based content moderation via the shipped classifier prompt,
    run on `model` — or, when `model` is None (the default), on the
    agent's own configured model: no hidden second provider, no second
    API key, keyless on FakeProvider (ledger #84).

    Modes:
    - NONE: No guardrails
    - POLICY_ONLY: Run custom policy (requires policy string)

    Error Policy:
    - FAIL_OPEN: On guardrail API error, treat as safe (default). Best for UX.
    - FAIL_CLOSED: On guardrail API error, treat as blocked. Best for security.

    Note: Output guardrails only work with stream=False.

    Example:
        from neosian import GuardrailsConfig, GuardrailMode, PolicyBuilder

        # Policy guardrails
        guardrails = GuardrailsConfig(
            input_mode=GuardrailMode.POLICY_ONLY,
            input_policy=PolicyBuilder.default(),
            block_on_input=True,
        )

        # High-security: block on any guardrail error
        guardrails = GuardrailsConfig(
            input_mode=GuardrailMode.POLICY_ONLY,
            input_policy=PolicyBuilder.default(),
            error_policy=GuardrailErrorPolicy.FAIL_CLOSED,
        )
    """

    # Input guardrails
    input_mode: GuardrailMode = GuardrailMode.NONE
    input_policy: str | None = None
    block_on_input: bool = True

    # Output guardrails (only work with stream=False). A flagged output
    # is blanked like a flagged input unless `block_on_output` is off,
    # which returns it verbatim, flagged, for the host to handle (NF #169).
    output_mode: GuardrailMode = GuardrailMode.NONE
    output_policy: str | None = None
    block_on_output: bool = True

    # Error handling policy; a classifier that exceeds `timeout_seconds`
    # is an error under it (FAIL_OPEN passes, FAIL_CLOSED blocks) — None
    # leaves the classifier call unbounded beyond the SDK's own timeout.
    error_policy: GuardrailErrorPolicy = GuardrailErrorPolicy.FAIL_OPEN
    timeout_seconds: float | None = None

    # Policy model; None = the agent's own configured model
    model: AnyModel | str | None = None

    def __post_init__(self) -> None:
        """Validate configuration."""
        if self.model is not None:
            self.model = resolve_model(self.model)
        # Check input policy requirement
        if self.input_mode != GuardrailMode.NONE and self.input_policy is None:
            raise ValueError(
                f"input_mode={self.input_mode.value} requires input_policy to be set"
            )

        # Check output policy requirement
        if self.output_mode != GuardrailMode.NONE and self.output_policy is None:
            raise ValueError(
                f"output_mode={self.output_mode.value} requires output_policy to be set"
            )
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError(
                f"timeout_seconds must be positive, got {self.timeout_seconds}"
            )

    @property
    def has_output_guardrails(self) -> bool:
        """Check if any output guardrails are configured."""
        return self.output_mode != GuardrailMode.NONE


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
        flagged_at: Where content was flagged ("input" or "output"), if any.
        input_policy: Policy result for input.
        output_policy: Policy result for output.
    """

    safe: bool
    flagged_at: Literal["input", "output"] | None = None
    input_policy: PolicyResult | None = None
    output_policy: PolicyResult | None = None

    @property
    def policy_rationale(self) -> str | None:
        """First available policy rationale (input or output)."""
        if self.input_policy and self.input_policy.rationale:
            return self.input_policy.rationale
        if self.output_policy and self.output_policy.rationale:
            return self.output_policy.rationale
        return None
