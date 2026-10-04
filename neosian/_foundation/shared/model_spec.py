"""Model capability, effort and integer rate-card vocabulary."""

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Final

from neosian._foundation.shared.catalog import OpenAICompatible


class Provider(str, Enum):
    """LLM Provider identifiers."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    CEREBRAS = "cerebras"
    # Registered models' shared row (DESIGN §19): a client is built from
    # the model's door, never from this row alone.
    OPENAI_COMPATIBLE = "openai_compatible"
    FAKE = "fake"


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

    @property
    def effective_cache_write_1h_per_mtok(self) -> int:
        """Cache-write rate for an hour-long breakpoint (NC9, #227).

        Derived rather than carded: the shipped rates bake in the 5m
        premium as absolute figures, and the hour costs twice base where
        five minutes cost 1.25x — a published multiple of a rate already
        verified, so no new column enters the card and the fingerprint
        keeps sealing what it sealed.
        """
        return 2 * self.input_per_mtok


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
    # A forced `tool_choice` on the wire: False on the Claude 5 flagships (#292).
    supports_forced_tool_choice: bool = True
    # Anthropic's compact-2026-01-12 beta — a capability of the row, never
    # inferred from the provider (N4).
    supports_compaction_blocks: bool = False
    pricing: ModelPricing | None = None
    # The door a compat row is served through (DESIGN §31): set on the
    # shipped door rows and on every registered model, None on a provider
    # adapter's row. Not part of the rate card the fingerprint seals.
    door: OpenAICompatible | None = None
    # The catalog clock (DESIGN §31, ROADMAP §NW): the provider's shutdown
    # or not-sooner-than date, and the date a stated card stops holding.
    # A keyless test fails `make test` inside 30 days of either.
    retires: date | None = None
    card_until: date | None = None
    supports_xhigh_effort: bool = False
    supports_no_effort: bool = False


class ReasoningEffort(str, Enum):
    """Reasoning effort level for supported models.

    Controls how many reasoning tokens the model uses. MAX is passed
    through only where the spec sets supports_max_effort; every client
    downgrades it to HIGH with a warning otherwise.
    """

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"
