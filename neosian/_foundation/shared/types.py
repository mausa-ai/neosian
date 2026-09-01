"""Type definitions for neosian.

All NewType definitions are centralized here.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NewType

from pydantic import BaseModel

from neosian._foundation.shared.context_policy import ContextPolicy
from neosian._foundation.shared.models import (
    DEFAULT_MODELS as DEFAULT_MODELS,
    MICRO_PER_USD as MICRO_PER_USD,
    PRICES_AS_OF as PRICES_AS_OF,
    PRICES_FINGERPRINT as PRICES_FINGERPRINT,
    ClientFactory as ClientFactory,
    Model as Model,
    ModelPricing as ModelPricing,
    ModelSpec as ModelSpec,
    Provider as Provider,
    ReasoningEffort as ReasoningEffort,
    format_micro_usd as format_micro_usd,
)

if TYPE_CHECKING:
    from neosian._foundation.agent.approval import ToolGateConfig
    from neosian._foundation.agent.hooks import AgentHooks
    from neosian._foundation.tools.base import ToolResult

# Core identifiers
AgentName = NewType("AgentName", str)
ToolName = NewType("ToolName", str)
ToolCallId = NewType("ToolCallId", str)
SkillName = NewType("SkillName", str)
BlackboardName = NewType("BlackboardName", str)

# Content types
SystemPrompt = NewType("SystemPrompt", str)
UserMessage = NewType("UserMessage", str)
AssistantMessage = NewType("AssistantMessage", str)

# Tool function type (defined here to avoid circular imports)
ToolFunction = Callable[..., Awaitable["ToolResult[Any]"]]


# =============================================================================
# Fallback Configuration
# =============================================================================


@dataclass
class FallbackConfig:
    """Configuration for model fallback behavior.

    When the primary model fails, the agent will fall back to this model.
    The fallback is "sticky" - once fallen back, subsequent calls continue
    using the fallback model until retry_main_after successful calls.

    Attributes:
        model: The fallback model to use when primary fails.
        retry_main_after: Number of successful fallback calls before retrying
            the main model. 0 means never retry main (stay on fallback).

    Example:
        from neosian import AgentConfig, FallbackConfig, Model

        config = AgentConfig(
            system_prompt="You are helpful.",
            model=Model.CLAUDE_OPUS_4_6,
            fallback=FallbackConfig(
                model=Model.GPT_5_PRO,
                retry_main_after=5,  # Try main again after 5 successful calls
            ),
        )
    """

    model: Model
    retry_main_after: int = 0


@dataclass
class FallbackState:
    """Runtime state for fallback tracking.

    Tracks whether we're currently using the fallback model and how many
    successful calls have been made since falling back. This state is
    managed internally by AgentSession for sticky fallback behavior.

    Attributes:
        using_fallback: True if currently using the fallback model.
        successful_fallback_calls: Count of successful calls since falling back.
    """

    using_fallback: bool = False
    successful_fallback_calls: int = 0


@dataclass
class ResponseFormat:
    """Structured output configuration.

    Specifies a Pydantic model or Union type that the LLM response must conform to.
    The LLM will be constrained to generate valid JSON matching the schema.

    Supports:
        - Single Pydantic BaseModel subclass
        - Discriminated Union types (Union[ModelA, ModelB])

    Note: Incompatible with stream=True. Structured outputs require complete
    responses for schema validation.

    Attributes:
        schema: Pydantic model class or Union type defining the output structure.
        strict: If True, model must exactly match schema. Defaults to True.

    Examples:
        # Single model
        from pydantic import BaseModel
        from neosian import Agent, AgentConfig, ResponseFormat

        class WeatherResponse(BaseModel):
            temperature: float
            conditions: str

        response = await agent.run(
            messages,
            stream=False,
            response_format=ResponseFormat(schema=WeatherResponse),
        )
        # response.parsed is a WeatherResponse instance

        # Discriminated Union
        from typing import Literal, Union

        class TTSOutput(BaseModel):
            type: Literal["tts"]
            audio_url: str

        class MusicOutput(BaseModel):
            type: Literal["music"]
            track_id: str

        response = await agent.run(
            messages,
            stream=False,
            response_format=ResponseFormat(schema=Union[TTSOutput, MusicOutput]),
        )
        # response.parsed is TTSOutput or MusicOutput instance
    """

    schema: type[BaseModel] | type  # Union types are `type` at runtime
    strict: bool = True


# Todo status enum
class TodoStatus(str, Enum):
    """Status of a todo item."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass(frozen=True)
class BlackboardEntry:
    """An entry in the blackboard listing.

    Represents the metadata of a blackboard entry (name and description).
    The actual content is read on-demand via the provider.

    Attributes:
        name: Unique identifier for the entry.
        description: Human-readable description shown when listing entries.
    """

    name: BlackboardName
    description: str


@dataclass(frozen=True)
class Skill:
    """A skill loaded from a markdown file.

    Skills are markdown instructions that the agent can discover and load
    on-demand via built-in tools. They are NOT injected into the system prompt.

    Attributes:
        name: Unique identifier for the skill.
        description: Human-readable description shown when listing skills.
        content: The markdown body (without frontmatter).
    """

    name: SkillName
    description: str
    content: str


@dataclass
class AgentConfig:
    """Configuration for an agent.

    This is the contract for one-file agent definitions.
    Export a `configuration` variable of this type.

    Example:
        from neosian import AgentConfig, Tool, ToolResult, Model, FallbackConfig

        @Tool(name="greet", description="Say hello")
        async def greet(name: str) -> ToolResult[str]:
            return ToolResult.ok(f"Hello {name}")

        configuration = AgentConfig(
            system_prompt="You are helpful.",
            tools=[greet],
            model=Model.CLAUDE_SONNET_5,
            fallback=FallbackConfig(
                model=Model.CEREBRAS_GPT_OSS_120B,
                retry_main_after=5,
            ),
        )
    """

    system_prompt: SystemPrompt
    tools: list[ToolFunction] = field(default_factory=list)
    model: Model = Model.CEREBRAS_GPT_OSS_120B
    fallback: FallbackConfig | None = None
    enable_todo: bool = True
    guardrails: "GuardrailsConfig | None" = None
    reasoning_effort: ReasoningEffort | None = None
    max_output_tokens: int | None = None
    max_parallel_tools: int | None = None
    max_retries: int | None = None
    cache_conversation: bool = True
    skill_dir: str | Path | None = None
    blackboard: Any = None  # BlackboardProvider | None (Any to avoid circular import)
    memory: Any = None  # MemoryConfig | None (Any to avoid circular import)
    client_factory: "ClientFactory | None" = None
    hooks: "AgentHooks | None" = None
    # Default-on, deliberately-underestimating pre-call window check
    # (DESIGN §5, ledger #16); None disables the proactive check.
    context_policy: ContextPolicy | None = ContextPolicy()
    # Anthropic-native memory transport (memory_20250818): the memory
    # tool's wire declaration goes schema-less and rides the trained
    # behavior; inert on every other provider, never raises (ledger #42).
    native_memory: bool = False
    # Anthropic server-side compaction (compact beta), threaded per-call
    # like cache_conversation; other providers ignore it. Validated at
    # the client (Haiku 4.5 is Anthropic yet unsupported), not here —
    # under fallback the answering model may not be `model`.
    server_compaction: bool = False
    # The tool-approval gate (DESIGN §17): every tool call passes through
    # the approver before executing; no decision denies (default-deny).
    tool_gate: "ToolGateConfig | None" = None

    # Internal: loaded skills (set by __post_init__)
    _skills: list[Skill] = field(default_factory=list, init=False, repr=False)

    @property
    def skills(self) -> list[Skill]:
        """Get loaded skills from skill_dir."""
        return self._skills

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        # Lazy import to avoid circular dependency at module load time
        from neosian._foundation.shared.constants import ErrorMessages, LLMDefaults
        from neosian._foundation.shared.exceptions import (
            InvalidModelError,
            UnsupportedParameterError,
        )

        # Apply default max_output_tokens from config
        if self.max_output_tokens is None:
            object.__setattr__(self, "max_output_tokens", LLMDefaults.MAX_OUTPUT_TOKENS)

        # Apply default max_parallel_tools from config
        if self.max_parallel_tools is None:
            object.__setattr__(
                self, "max_parallel_tools", LLMDefaults.MAX_PARALLEL_TOOLS
            )

        # Apply default transport-level retries from config
        if self.max_retries is None:
            object.__setattr__(self, "max_retries", LLMDefaults.MAX_RETRIES)

        # Runtime validation - model could be anything if user bypasses type hints
        model: object = self.model  # Type erasure to enable isinstance check
        if not isinstance(model, Model):
            supported = ", ".join(f"Model.{m.name}" for m in Model)
            message = ErrorMessages.INVALID_MODEL.format(
                model_type=type(model).__name__,
                model_value=model,
                supported_models=supported,
            )
            raise InvalidModelError(message, model)

        # Validate reasoning_effort is only used with models that support it
        if self.reasoning_effort is not None and not self.model.supports_reasoning:
            reasoning_models = [m for m in Model if m.supports_reasoning]
            supported_models = ", ".join(f"Model.{m.name}" for m in reasoning_models)
            raise UnsupportedParameterError(
                ErrorMessages.REASONING_EFFORT_MODEL_MISMATCH.format(
                    model=self.model.value,
                    supported_models=supported_models,
                )
            )

        # Validate max_output_tokens
        assert self.max_output_tokens is not None  # Set above
        if self.max_output_tokens < 1:
            raise UnsupportedParameterError(
                ErrorMessages.MAX_OUTPUT_TOKENS_INVALID.format(
                    requested=self.max_output_tokens,
                )
            )
        if self.max_output_tokens > self.model.max_output_tokens:
            raise UnsupportedParameterError(
                ErrorMessages.MAX_OUTPUT_TOKENS_EXCEEDED.format(
                    requested=self.max_output_tokens,
                    model=self.model.value,
                    limit=self.model.max_output_tokens,
                )
            )

        # Validate max_retries
        assert self.max_retries is not None  # Set above
        if self.max_retries < 0:
            raise UnsupportedParameterError(
                ErrorMessages.MAX_RETRIES_INVALID.format(requested=self.max_retries)
            )

        # Validate max_parallel_tools
        assert self.max_parallel_tools is not None  # Set above
        if self.max_parallel_tools < 1:
            raise UnsupportedParameterError(
                ErrorMessages.INVALID_MAX_PARALLEL_TOOLS.format(
                    value=self.max_parallel_tools,
                )
            )

        # Load skills from directory if configured
        if self.skill_dir is not None:
            from neosian._foundation.shared.skill import load_skills

            loaded = load_skills(self.skill_dir)
            object.__setattr__(self, "_skills", loaded)


# Guardrail types
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

    # Output guardrails (only work with stream=False)
    output_mode: GuardrailMode = GuardrailMode.NONE
    output_policy: str | None = None

    # Error handling policy
    error_policy: GuardrailErrorPolicy = GuardrailErrorPolicy.FAIL_OPEN

    # Policy model; None = the agent's own configured model
    model: Model | None = None

    def __post_init__(self) -> None:
        """Validate configuration."""
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
