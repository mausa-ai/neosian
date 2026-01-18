"""Type definitions for neosian.

All NewType definitions are centralized here.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, NewType

if TYPE_CHECKING:
    from neosian._foundation.tools.base import ToolResult

# Core identifiers
AgentName = NewType("AgentName", str)
ToolName = NewType("ToolName", str)
ToolCallId = NewType("ToolCallId", str)

# Content types
SystemPrompt = NewType("SystemPrompt", str)
UserMessage = NewType("UserMessage", str)
AssistantMessage = NewType("AssistantMessage", str)

# Tool function type (defined here to avoid circular imports)
ToolFunction = Callable[..., Awaitable["ToolResult[Any]"]]


# =============================================================================
# Provider and Model Enums
# =============================================================================


class Provider(str, Enum):
    """LLM Provider identifiers."""

    GROQ = "groq"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


# Model to provider mapping (populated after Model enum is defined)
_MODEL_PROVIDERS: dict[str, Provider] = {}

# Model to max output tokens mapping
_MODEL_MAX_TOKENS: dict[str, int] = {}

# Default max output tokens (used when not specified)
_DEFAULT_MAX_TOKENS = 8192


class Model(str, Enum):
    """Supported LLM models."""

    # Groq - Production
    LLAMA_3_3_70B = "llama-3.3-70b-versatile"
    LLAMA_3_1_8B = "llama-3.1-8b-instant"
    GPT_OSS_120B = "openai/gpt-oss-120b"
    GPT_OSS_20B = "openai/gpt-oss-20b"

    # Groq - Preview
    LLAMA_4_MAVERICK_17B = "meta-llama/llama-4-maverick-17b-128e-instruct"
    LLAMA_4_SCOUT_17B = "meta-llama/llama-4-scout-17b-16e-instruct"
    QWEN3_32B = "qwen/qwen3-32b"
    KIMI_K2 = "moonshotai/kimi-k2-instruct"
    KIMI_K2_0905 = "moonshotai/kimi-k2-instruct-0905"

    # Groq - Guardrails
    LLAMA_GUARD_4_12B = "meta-llama/llama-guard-4-12b"
    GPT_OSS_SAFEGUARD_20B = "openai/gpt-oss-safeguard-20b"

    # OpenAI
    GPT_5_1 = "gpt-5.1-2025-11-13"
    GPT_5_MINI = "gpt-5-mini-2025-08-07"
    GPT_5_NANO = "gpt-5-nano-2025-08-07"
    GPT_5_PRO = "gpt-5-pro-2025-10-06"

    # Anthropic
    CLAUDE_OPUS_4_5 = "claude-opus-4-5-20251101"
    CLAUDE_SONNET_4_5 = "claude-sonnet-4-5-20250929"
    CLAUDE_HAIKU_4_5 = "claude-haiku-4-5-20251001"

    @property
    def provider(self) -> Provider:
        """Get the provider for this model."""
        return _MODEL_PROVIDERS[self.value]

    @property
    def max_output_tokens(self) -> int:
        """Get max output tokens for this model."""
        return _MODEL_MAX_TOKENS.get(self.value, _DEFAULT_MAX_TOKENS)


# Populate provider mappings
_GROQ_MODELS = {
    Model.LLAMA_3_3_70B,
    Model.LLAMA_3_1_8B,
    Model.GPT_OSS_120B,
    Model.GPT_OSS_20B,
    Model.LLAMA_4_MAVERICK_17B,
    Model.LLAMA_4_SCOUT_17B,
    Model.QWEN3_32B,
    Model.KIMI_K2,
    Model.KIMI_K2_0905,
    Model.LLAMA_GUARD_4_12B,
    Model.GPT_OSS_SAFEGUARD_20B,
}

_OPENAI_MODELS = {
    Model.GPT_5_1,
    Model.GPT_5_MINI,
    Model.GPT_5_NANO,
    Model.GPT_5_PRO,
}

_ANTHROPIC_MODELS = {
    Model.CLAUDE_OPUS_4_5,
    Model.CLAUDE_SONNET_4_5,
    Model.CLAUDE_HAIKU_4_5,
}

for m in _GROQ_MODELS:
    _MODEL_PROVIDERS[m.value] = Provider.GROQ

for m in _OPENAI_MODELS:
    _MODEL_PROVIDERS[m.value] = Provider.OPENAI

for m in _ANTHROPIC_MODELS:
    _MODEL_PROVIDERS[m.value] = Provider.ANTHROPIC
    _MODEL_MAX_TOKENS[m.value] = 8192  # 64k for Claude 4.5 models


# Default models per provider
DEFAULT_MODELS: dict[Provider, Model] = {
    Provider.GROQ: Model.GPT_OSS_20B,
    Provider.OPENAI: Model.GPT_5_NANO,
    Provider.ANTHROPIC: Model.CLAUDE_SONNET_4_5,
}


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
        from neosian import AgentConfig, Tool, ToolResult, Model

        @Tool(name="greet", description="Say hello")
        async def greet(name: str) -> ToolResult[str]:
            return ToolResult.ok(f"Hello {name}")

        configuration = AgentConfig(
            system_prompt="You are helpful.",
            tools=[greet],
            model=Model.LLAMA_3_3_70B,
        )
    """

    system_prompt: SystemPrompt
    tools: list[ToolFunction] = field(default_factory=list)
    model: Model = Model.GPT_OSS_20B
    enable_todo: bool = True
    guardrails: "GuardrailsConfig | None" = None

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        # Runtime validation - model could be anything if user bypasses type hints
        model: object = self.model  # Type erasure to enable isinstance check
        if not isinstance(model, Model):
            # Lazy import to avoid circular dependency at module load time
            # (types -> exceptions -> constants -> types)
            from neosian._foundation.shared.exceptions import InvalidModelError

            supported = ", ".join(f"Model.{m.name}" for m in Model)
            message = (
                f"Invalid model: expected Model enum, got {type(model).__name__} "
                f"with value '{model}'. Supported models: {supported}"
            )
            raise InvalidModelError(message, model)


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

    Two guardrail types:
    - Classifier (Llama Guard 4): Fast, fixed taxonomy (S1-S14)
    - Policy (GPT-OSS-Safeguard): Custom rules, flexible

    Modes:
    - NONE: No guardrails
    - CLASSIFIER_ONLY: Run Llama Guard only
    - POLICY_ONLY: Run custom policy only (requires policy string)
    - CLASSIFIER_AND_POLICY: Run both, always
    - CLASSIFIER_THEN_POLICY: Run policy only if classifier flags (optimization)

    Error Policy:
    - FAIL_OPEN: On guardrail API error, treat as safe (default). Best for UX.
    - FAIL_CLOSED: On guardrail API error, treat as blocked. Best for security.

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

        # High-security: block on any guardrail error
        guardrails = GuardrailsConfig(
            input_mode=GuardrailMode.CLASSIFIER_ONLY,
            error_policy=GuardrailErrorPolicy.FAIL_CLOSED,
        )
    """

    # Input guardrails
    input_mode: GuardrailMode = GuardrailMode.NONE
    input_policy: str | None = None
    block_on_input: bool = True

    # Output guardrails (only work with stream=False)
    output_mode: GuardrailMode = GuardrailMode.NONE
    output_policy: str | None = None

    # Error handling policy
    error_policy: GuardrailErrorPolicy = GuardrailErrorPolicy.FAIL_OPEN

    def __post_init__(self) -> None:
        """Validate configuration."""
        # Check input policy requirement
        if self.input_mode.uses_policy() and self.input_policy is None:
            raise ValueError(
                f"input_mode={self.input_mode.value} requires input_policy to be set"
            )

        # Check output policy requirement
        if self.output_mode.uses_policy() and self.output_policy is None:
            raise ValueError(
                f"output_mode={self.output_mode.value} requires output_policy to be set"
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
        flagged_at: Where content was flagged ("input" or "output"), if any.
        input_classifier: Llama Guard result for input.
        input_policy: Policy result for input.
        output_classifier: Llama Guard result for output.
        output_policy: Policy result for output.
    """

    safe: bool
    flagged_at: Literal["input", "output"] | None = None
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


# Evaluation types
@dataclass
class Expectation:
    """Expected behavior for an evaluation turn.

    Attributes:
        tool: Expected tool name to be called (None if no_tool=True).
        params: Expected parameters (use "_exists" to check presence only).
        no_tool: If True, expects no tool call (just a response).
        sequence: Expected tool call sequence for multi-step flows.
    """

    tool: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    no_tool: bool = False
    sequence: list[dict[str, Any]] | None = None


@dataclass
class EvalTurn:
    """Single turn in a conversational evaluation case.

    Attributes:
        user: User message for this turn.
        expect: Expected behavior after this turn.
        mock_response: Optional mock tool response for this turn.
            Used to build realistic context for subsequent turns.
            If None, defaults to {"success": true}.
    """

    user: str
    expect: Expectation
    mock_response: dict[str, Any] | None = None


@dataclass
class EvalCase:
    """Single evaluation test case.

    Supports both one-shot and conversational cases:
    - One-shot: input + expect fields are set
    - Conversational: conversation field is set with list of turns

    Attributes:
        name: Unique identifier for this case.
        input: User input for one-shot cases.
        expect: Expected behavior for one-shot cases.
        conversation: List of turns for conversational cases.
    """

    name: str
    input: str | None = None
    expect: Expectation | None = None
    conversation: list[EvalTurn] | None = None

    @property
    def is_conversational(self) -> bool:
        """Check if this is a conversational case."""
        return self.conversation is not None

    @property
    def total_turns(self) -> int:
        """Get total number of turns to evaluate."""
        if self.conversation:
            return len(self.conversation)
        return 1


@dataclass
class EvalConfig:
    """Configuration for an evaluation run.

    Supports two modes:
    1. Legacy mode: prompts list contains Python agent files
    2. Variant mode: agent is a Python file, prompts are YAML configs

    Attributes:
        name: Name/description of this evaluation.
        prompts: List of prompt file paths to test (Python or YAML).
        models: List of model identifiers to test.
        cases: List of evaluation cases.
        agent: Optional path to Python agent file (for variant mode).
        stop_on_failure: If True, stop conversational cases on first turn failure.
            If False, run all turns to get the full picture. Defaults to True.
    """

    name: str
    prompts: list[str]
    models: list[str]
    cases: list[EvalCase]
    agent: str | None = None
    stop_on_failure: bool = True

    @property
    def is_variant_mode(self) -> bool:
        """Check if this config uses variant mode (agent + YAML prompts)."""
        return self.agent is not None


@dataclass
class TurnResult:
    """Result of evaluating a single turn.

    Attributes:
        turn_index: Index of this turn in the conversation (0 for one-shot).
        passed: Whether all expectations were met.
        expected_tool: Tool that was expected.
        actual_tool: Tool that was actually called (None if no tool).
        expected_params: Parameters that were expected.
        actual_params: Parameters that were actually passed.
        param_failures: List of parameter expectation failures.
        expected_no_tool: Whether no tool was expected.
        actual_response: Actual assistant response content.
        error: Error message if evaluation failed.
    """

    turn_index: int
    passed: bool
    expected_tool: str | None = None
    actual_tool: str | None = None
    expected_params: dict[str, Any] = field(default_factory=dict)
    actual_params: dict[str, Any] = field(default_factory=dict)
    param_failures: list[str] = field(default_factory=list)
    expected_no_tool: bool = False
    actual_response: str | None = None
    error: str | None = None


@dataclass
class EvalResult:
    """Result of evaluating one case.

    Attributes:
        case_name: Name of the evaluated case.
        prompt_file: Path to the prompt file used.
        model: Model identifier used.
        passed: Whether all turns passed.
        turns: Results for each turn.
        tool_sequence: Complete sequence of tools called.
        error: Error message if evaluation failed to run.
        latency_ms: Response time in ms (TTFT if streaming, full response if not).
    """

    case_name: str
    prompt_file: str
    model: str
    passed: bool
    turns: list[TurnResult] = field(default_factory=list)
    tool_sequence: list[str] = field(default_factory=list)
    error: str | None = None
    latency_ms: float = 0.0

    @property
    def pass_count(self) -> int:
        """Number of turns that passed."""
        return sum(1 for t in self.turns if t.passed)

    @property
    def total_turns(self) -> int:
        """Total number of turns evaluated."""
        return len(self.turns)


@dataclass
class ToolCallCapture:
    """Captured tool call during evaluation.

    Attributes:
        name: Tool name that was called.
        arguments: Arguments passed to the tool.
    """

    name: str
    arguments: dict[str, Any]


@dataclass
class ToolPromptConfig:
    """Prompting configuration for a tool (from YAML).

    Attributes:
        description: Tool description shown to the LLM.
        on_success: Hint to inject after successful tool execution.
    """

    description: str
    on_success: str | None = None


@dataclass
class PromptConfig:
    """Agent prompting configuration loaded from YAML.

    Defines system prompt and tool descriptions/hints without implementations.
    Used for eval variants to test different prompting strategies.

    Attributes:
        system_prompt: The system prompt for the agent.
        tools: Tool name to prompting config mapping.
        compact_summarize: Optional summarization prompt.
    """

    system_prompt: str
    tools: dict[str, ToolPromptConfig] = field(default_factory=dict)
    compact_summarize: str | None = None


@dataclass
class EvalVariant:
    """A variant configuration for evaluation.

    Represents one prompting strategy to test against the base agent.

    Attributes:
        name: Unique identifier for this variant.
        config: Path to the YAML prompt config file.
    """

    name: str
    config: str
