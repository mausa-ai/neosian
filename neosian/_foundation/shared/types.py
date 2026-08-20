"""Type definitions for neosian.

All NewType definitions are centralized here.
"""

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, NewType

from pydantic import BaseModel

from neosian._foundation.shared.context_policy import ContextPolicy

if TYPE_CHECKING:
    from neosian._foundation.agent.hooks import AgentHooks
    from neosian._foundation.llm.base import BaseLLMClient
    from neosian._foundation.tools.base import ToolResult

# Core identifiers
AgentName = NewType("AgentName", str)
ToolName = NewType("ToolName", str)
ToolCallId = NewType("ToolCallId", str)
PlaybookName = NewType("PlaybookName", str)
BlackboardName = NewType("BlackboardName", str)

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
    CEREBRAS = "cerebras"
    FAKE = "fake"


# Factory seam for injecting clients (scripted fakes, host wiring) without
# touching the router (DESIGN §2). BaseLLMClient import stays type-only —
# shared must not import llm at runtime.
ClientFactory = Callable[[Provider], "BaseLLMClient"]


# Date the pricing table below was last verified against provider price lists.
PRICES_AS_OF = "2026-08-18"

# Integer micro-USD per USD — money is int µ$ everywhere (ECOSYSTEM §4);
# floats exist only at display edges (format_micro_usd).
MICRO_PER_USD: Final = 1_000_000


def format_micro_usd(micro: int, *, places: int = 4) -> str:
    """Render integer micro-USD as a dollar string — the display edge.

    The one place a float touches money; it never re-enters the vocabulary.
    """
    return f"${micro / MICRO_PER_USD:.{places}f}"


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """List prices in integer micro-USD per million tokens (ECOSYSTEM §4).

    Approximate, for observability/cost-tracking — not a billing source.
    None cache rates mean "no separate published rate"; the effective_*
    properties fall back to the input rate (conservative upper bound).
    """

    input_per_mtok: int
    output_per_mtok: int
    cache_read_per_mtok: int | None = None
    cache_write_per_mtok: int | None = None

    @property
    def effective_cache_read_per_mtok(self) -> int:
        """Cache-read rate, falling back to the input rate."""
        if self.cache_read_per_mtok is not None:
            return self.cache_read_per_mtok
        return self.input_per_mtok

    @property
    def effective_cache_write_per_mtok(self) -> int:
        """Cache-write rate, falling back to the input rate."""
        if self.cache_write_per_mtok is not None:
            return self.cache_write_per_mtok
        return self.input_per_mtok


@dataclass(frozen=True)
class ModelSpec:
    """Immutable specification for a model's capabilities.

    supports_images / supports_documents describe what neosian's converters
    implement, not the raw provider capability (e.g. GPT-5 has vision
    upstream, but neosian's OpenAI converter does not — so it stays False).

    pricing is None for models without verified list prices;
    Usage.cost_micro_usd() returns None for those.
    """

    provider: Provider
    context_window: int
    max_output_tokens: int
    supports_reasoning: bool = False
    supports_images: bool = False
    supports_documents: bool = False
    supports_max_effort: bool = False
    # Anthropic's compact-2026-01-12 beta — a provider check would be
    # wrong: Haiku 4.5 is Anthropic and outside the support set (N4).
    supports_compaction_blocks: bool = False
    pricing: ModelPricing | None = None


# Model specs registry (populated after Model enum is defined)
_MODEL_SPECS: dict[str, ModelSpec] = {}


class ReasoningEffort(str, Enum):
    """Reasoning effort level for supported models.

    Controls how many reasoning tokens the model uses.
    Supported by GPT-OSS models (Groq), GPT-5 models (OpenAI),
    and reasoning-capable Claude models (Anthropic).
    Note: MAX is only passed through for models whose spec sets
    supports_max_effort; Groq and OpenAI downgrade MAX to HIGH with a warning.
    Note: GPT-5-Pro only supports HIGH; other values are forced to HIGH with a warning.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAX = "max"


class Model(str, Enum):
    """Supported LLM models."""

    # Groq - Production
    GROQ_GPT_OSS_120B = "openai/gpt-oss-120b"
    GROQ_GPT_OSS_20B = "openai/gpt-oss-20b"

    # Groq - Preview
    GROQ_QWEN3_6_27B = "qwen/qwen3.6-27b"

    # Groq - Guardrails
    GROQ_GPT_OSS_SAFEGUARD_20B = "openai/gpt-oss-safeguard-20b"

    # OpenAI
    GPT_5_1 = "gpt-5.1-2025-11-13"
    GPT_5_MINI = "gpt-5-mini-2025-08-07"
    GPT_5_NANO = "gpt-5-nano-2025-08-07"
    GPT_5_PRO = "gpt-5-pro-2025-10-06"

    # Anthropic
    CLAUDE_OPUS_5 = "claude-opus-5"
    CLAUDE_OPUS_4_6 = "claude-opus-4-6"
    CLAUDE_SONNET_5 = "claude-sonnet-5"
    CLAUDE_HAIKU_4_5 = "claude-haiku-4-5-20251001"

    # Cerebras - Production
    CEREBRAS_GPT_OSS_120B = "gpt-oss-120b"

    # Cerebras - Preview
    CEREBRAS_GEMMA_4_31B = "gemma-4-31b"

    # Fake (deterministic, keyless — public surface in neosian.fake)
    FAKE = "fake"
    FAKE_SMALL = "fake-small"
    FAKE_REASONING = "fake-reasoning"

    @property
    def spec(self) -> ModelSpec:
        """Get the full specification for this model."""
        return _MODEL_SPECS[self.value]

    @property
    def provider(self) -> Provider:
        """Get the provider for this model."""
        return _MODEL_SPECS[self.value].provider

    @property
    def max_output_tokens(self) -> int:
        """Get max output tokens (API ceiling) for this model."""
        return _MODEL_SPECS[self.value].max_output_tokens

    @property
    def context_window(self) -> int:
        """Get context window size for this model."""
        return _MODEL_SPECS[self.value].context_window

    @property
    def supports_reasoning(self) -> bool:
        """Check if this model supports reasoning_effort parameter."""
        return _MODEL_SPECS[self.value].supports_reasoning

    @property
    def supports_images(self) -> bool:
        """Check if neosian's converter supports image content for this model."""
        return _MODEL_SPECS[self.value].supports_images

    @property
    def supports_documents(self) -> bool:
        """Check if neosian's converter supports document content for this model."""
        return _MODEL_SPECS[self.value].supports_documents

    @property
    def supports_max_effort(self) -> bool:
        """Check if this model accepts reasoning_effort=MAX without downgrade."""
        return _MODEL_SPECS[self.value].supports_max_effort

    @property
    def supports_compaction_blocks(self) -> bool:
        """Check if this model supports Anthropic server-side compaction."""
        return _MODEL_SPECS[self.value].supports_compaction_blocks

    @property
    def pricing(self) -> "ModelPricing | None":
        """Get list pricing for this model (None if not verified)."""
        return _MODEL_SPECS[self.value].pricing


# Groq - Production
_MODEL_SPECS[Model.GROQ_GPT_OSS_120B.value] = ModelSpec(
    provider=Provider.GROQ,
    context_window=131_072,
    max_output_tokens=65_536,
    supports_reasoning=True,
    pricing=ModelPricing(input_per_mtok=150_000, output_per_mtok=750_000),
)
_MODEL_SPECS[Model.GROQ_GPT_OSS_20B.value] = ModelSpec(
    provider=Provider.GROQ,
    context_window=131_072,
    max_output_tokens=65_536,
    supports_reasoning=True,
    pricing=ModelPricing(input_per_mtok=100_000, output_per_mtok=500_000),
)

# Groq - Preview
_MODEL_SPECS[Model.GROQ_QWEN3_6_27B.value] = ModelSpec(
    provider=Provider.GROQ,
    context_window=131_072,
    max_output_tokens=32_768,
)

# Groq - Guardrails
_MODEL_SPECS[Model.GROQ_GPT_OSS_SAFEGUARD_20B.value] = ModelSpec(
    provider=Provider.GROQ,
    context_window=131_072,
    max_output_tokens=65_536,
    pricing=ModelPricing(input_per_mtok=100_000, output_per_mtok=500_000),
)

# OpenAI
_MODEL_SPECS[Model.GPT_5_1.value] = ModelSpec(
    provider=Provider.OPENAI,
    context_window=400_000,
    max_output_tokens=128_000,
    supports_reasoning=True,
    pricing=ModelPricing(
        input_per_mtok=1_250_000,
        output_per_mtok=10_000_000,
        cache_read_per_mtok=125_000,
    ),
)
_MODEL_SPECS[Model.GPT_5_MINI.value] = ModelSpec(
    provider=Provider.OPENAI,
    context_window=400_000,
    max_output_tokens=128_000,
    supports_reasoning=True,
    pricing=ModelPricing(
        input_per_mtok=250_000,
        output_per_mtok=2_000_000,
        cache_read_per_mtok=25_000,
    ),
)
_MODEL_SPECS[Model.GPT_5_NANO.value] = ModelSpec(
    provider=Provider.OPENAI,
    context_window=400_000,
    max_output_tokens=128_000,
    supports_reasoning=True,
    pricing=ModelPricing(
        input_per_mtok=50_000,
        output_per_mtok=400_000,
        cache_read_per_mtok=5_000,
    ),
)
_MODEL_SPECS[Model.GPT_5_PRO.value] = ModelSpec(
    provider=Provider.OPENAI,
    context_window=400_000,
    max_output_tokens=128_000,
    supports_reasoning=True,
    pricing=ModelPricing(input_per_mtok=15_000_000, output_per_mtok=120_000_000),
)

# Anthropic
_MODEL_SPECS[Model.CLAUDE_OPUS_5.value] = ModelSpec(
    provider=Provider.ANTHROPIC,
    context_window=1_000_000,
    max_output_tokens=128_000,
    supports_reasoning=True,
    supports_images=True,
    supports_documents=True,
    supports_max_effort=True,
    supports_compaction_blocks=True,
    pricing=ModelPricing(
        input_per_mtok=5_000_000,
        output_per_mtok=25_000_000,
        cache_read_per_mtok=500_000,
        cache_write_per_mtok=6_250_000,
    ),
)
_MODEL_SPECS[Model.CLAUDE_OPUS_4_6.value] = ModelSpec(
    provider=Provider.ANTHROPIC,
    context_window=1_000_000,
    max_output_tokens=128_000,
    supports_reasoning=True,
    supports_images=True,
    supports_documents=True,
    supports_max_effort=True,
    supports_compaction_blocks=True,
    pricing=ModelPricing(
        input_per_mtok=5_000_000,
        output_per_mtok=25_000_000,
        cache_read_per_mtok=500_000,
        cache_write_per_mtok=6_250_000,
    ),
)
_MODEL_SPECS[Model.CLAUDE_SONNET_5.value] = ModelSpec(
    provider=Provider.ANTHROPIC,
    context_window=1_000_000,
    max_output_tokens=128_000,
    supports_reasoning=True,
    supports_images=True,
    supports_documents=True,
    supports_max_effort=True,
    supports_compaction_blocks=True,
    pricing=ModelPricing(
        input_per_mtok=3_000_000,
        output_per_mtok=15_000_000,
        cache_read_per_mtok=300_000,
        cache_write_per_mtok=3_750_000,
    ),
)
_MODEL_SPECS[Model.CLAUDE_HAIKU_4_5.value] = ModelSpec(
    provider=Provider.ANTHROPIC,
    context_window=200_000,
    max_output_tokens=64_000,
    supports_images=True,
    supports_documents=True,
    pricing=ModelPricing(
        input_per_mtok=1_000_000,
        output_per_mtok=5_000_000,
        cache_read_per_mtok=100_000,
        cache_write_per_mtok=1_250_000,
    ),
)

# Cerebras - Production
_MODEL_SPECS[Model.CEREBRAS_GPT_OSS_120B.value] = ModelSpec(
    provider=Provider.CEREBRAS,
    context_window=131_072,
    max_output_tokens=40_960,
    supports_reasoning=True,
    pricing=ModelPricing(input_per_mtok=250_000, output_per_mtok=690_000),
)

# Cerebras - Preview
_MODEL_SPECS[Model.CEREBRAS_GEMMA_4_31B.value] = ModelSpec(
    provider=Provider.CEREBRAS,
    context_window=131_072,
    max_output_tokens=32_768,
)

# Fake — deterministic keyless models (ECOSYSTEM §7). The capability split
# (FAKE carries media, FAKE_SMALL none; FAKE_SMALL's window is small) makes
# capability-aware fallback and context policy testable without keys, and
# the round, distinct rates make per-model cost attribution hand-computable.
_MODEL_SPECS[Model.FAKE.value] = ModelSpec(
    provider=Provider.FAKE,
    context_window=128_000,
    max_output_tokens=8_192,
    supports_images=True,
    supports_documents=True,
    pricing=ModelPricing(
        input_per_mtok=1_000_000,
        output_per_mtok=10_000_000,
        cache_read_per_mtok=100_000,
        cache_write_per_mtok=1_250_000,
    ),
)
_MODEL_SPECS[Model.FAKE_SMALL.value] = ModelSpec(
    provider=Provider.FAKE,
    context_window=8_192,
    max_output_tokens=8_192,
    pricing=ModelPricing(
        input_per_mtok=100_000,
        output_per_mtok=1_000_000,
        cache_read_per_mtok=10_000,
        cache_write_per_mtok=125_000,
    ),
)
_MODEL_SPECS[Model.FAKE_REASONING.value] = ModelSpec(
    provider=Provider.FAKE,
    context_window=128_000,
    max_output_tokens=8_192,
    supports_reasoning=True,
    supports_max_effort=True,
    pricing=ModelPricing(
        input_per_mtok=1_000_000,
        output_per_mtok=10_000_000,
        cache_read_per_mtok=100_000,
        cache_write_per_mtok=1_250_000,
    ),
)

# Default models per provider
DEFAULT_MODELS: dict[Provider, Model] = {
    Provider.GROQ: Model.GROQ_GPT_OSS_20B,
    Provider.OPENAI: Model.GPT_5_NANO,
    Provider.ANTHROPIC: Model.CLAUDE_SONNET_5,
    Provider.CEREBRAS: Model.CEREBRAS_GPT_OSS_120B,
    Provider.FAKE: Model.FAKE,
}


def _prices_fingerprint() -> str:
    """Canonical sha256 of the shipped rate card + its as-of date.

    A unit test recomputes this against PRICES_FINGERPRINT, so any price
    edit fails CI until the fingerprint (and, with it, PRICES_AS_OF) is
    bumped in the same commit — a gate, not a promise.
    """
    lines = [f"as_of:{PRICES_AS_OF}"]
    for model_id in sorted(_MODEL_SPECS):
        spec = _MODEL_SPECS[model_id]
        # Fake-model rates are test fixtures, not provider prices.
        if spec.pricing is None or spec.provider is Provider.FAKE:
            continue
        pricing = spec.pricing
        lines.append(
            f"{model_id}:{pricing.input_per_mtok}:{pricing.output_per_mtok}"
            f":{pricing.cache_read_per_mtok}:{pricing.cache_write_per_mtok}"
        )
    return hashlib.sha256("\n".join(lines).encode("ascii")).hexdigest()


PRICES_FINGERPRINT = "338de9420b8bfc345d22ff37ad7c5770c3272038b9c960555e823770f6cce5dc"


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
class Playbook:
    """A playbook loaded from a markdown file.

    Playbooks are markdown instructions that the agent can discover and load
    on-demand via built-in tools. They are NOT injected into the system prompt.

    Attributes:
        name: Unique identifier for the playbook.
        description: Human-readable description shown when listing playbooks.
        content: The markdown body (without frontmatter).
    """

    name: PlaybookName
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
            model=Model.GROQ_GPT_OSS_120B,
            fallback=FallbackConfig(
                model=Model.GROQ_GPT_OSS_20B,
                retry_main_after=5,
            ),
        )
    """

    system_prompt: SystemPrompt
    tools: list[ToolFunction] = field(default_factory=list)
    model: Model = Model.GROQ_GPT_OSS_20B
    fallback: FallbackConfig | None = None
    enable_todo: bool = True
    guardrails: "GuardrailsConfig | None" = None
    reasoning_effort: ReasoningEffort | None = None
    max_output_tokens: int | None = None
    max_parallel_tools: int | None = None
    max_retries: int | None = None
    cache_conversation: bool = True
    playbook_dir: str | Path | None = None
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

    # Internal: loaded playbooks (set by __post_init__)
    _playbooks: list[Playbook] = field(default_factory=list, init=False, repr=False)

    @property
    def playbooks(self) -> list[Playbook]:
        """Get loaded playbooks from playbook_dir."""
        return self._playbooks

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

        # Load playbooks from directory if configured
        if self.playbook_dir is not None:
            from neosian._foundation.shared.playbook import load_playbooks

            loaded = load_playbooks(self.playbook_dir)
            object.__setattr__(self, "_playbooks", loaded)


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

    Uses GPT-OSS-Safeguard for custom policy-based content moderation.

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
