"""The model registry: providers, models, capabilities, the rate card.

Money is integer micro-USD (ECOSYSTEM §4). PRICES_FINGERPRINT gates the
shipped rate card: a price edit fails CI until the fingerprint and
PRICES_AS_OF move in the same commit (DESIGN §4).
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from neosian._foundation.llm.base import BaseLLMClient


# =============================================================================
# Provider and Model Enums
# =============================================================================


class Provider(str, Enum):
    """LLM Provider identifiers."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    CEREBRAS = "cerebras"
    # Registered models' shared row (DESIGN §19): a client is built from
    # the model's door, never from this row alone.
    OPENAI_COMPATIBLE = "openai_compatible"
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
    Supported by GPT-OSS models (Cerebras), GPT-5 models (OpenAI),
    and reasoning-capable Claude models (Anthropic).
    Note: MAX is only passed through for models whose spec sets
    supports_max_effort; OpenAI-compatible providers downgrade MAX to
    HIGH with a warning.
    Note: GPT-5-Pro only supports HIGH; other values are forced to HIGH with a warning.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAX = "max"


class Model(str, Enum):
    """Supported LLM models."""

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


PRICES_FINGERPRINT = "3e59b9463e7aee2a3fe28798c13cd97aad3bea2ec1e6a198fa613e4dcb40c025"
