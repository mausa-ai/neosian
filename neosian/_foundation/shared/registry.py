"""The open model surface (DESIGN §19).

An `OpenAICompatible` door names where an OpenAI-compatible endpoint
lives, which environment variable signs requests to it, and the dialect
quirks its wire has. A `RegisteredModel` is `Model`'s structural twin —
the same capability properties over a `ModelSpec` — so every consumer
that reads a model reads both. The registry is configuration, not run
state: process-global, string-keyed, write-once per id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from neosian._foundation.shared.exceptions import ConfigurationError
from neosian._foundation.shared.models import Model, ModelPricing, ModelSpec, Provider

_DOOR_NAME: Final = re.compile(r"\A[a-z][a-z0-9_-]{0,63}\Z")
_ENV_NAME: Final = re.compile(r"\A[A-Z][A-Z0-9_]*\Z")


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenAICompatible:
    """An OpenAI-compatible endpoint and the dialect its wire speaks.

    `name` is the provider label wherever one is rendered for a model on
    this door — errors, the ready frame, the picker. `base_url=None`
    keeps the SDK's own endpoint (OpenAI's, or `OPENAI_BASE_URL`), which
    is how a fine-tuned OpenAI id the enum lacks gets registered. The
    dialect knobs default to OpenAI's behavior; a knob is earned by a
    measured need.
    """

    name: str
    api_key_env: str
    base_url: str | None = None
    temperature: bool = False
    reasoning_effort: bool = True
    reasoning_field: str | None = None
    strict_schemas: bool = True

    def __post_init__(self) -> None:
        if not _DOOR_NAME.match(self.name):
            raise ConfigurationError(
                f"door name {self.name!r}: use lowercase letters, digits, '-' or "
                "'_', starting with a letter (at most 64 characters)"
            )
        if not _ENV_NAME.match(self.api_key_env):
            raise ConfigurationError(
                f"door {self.name!r}: api_key_env {self.api_key_env!r} must be an "
                "environment variable name (uppercase letters, digits, '_')"
            )
        if self.base_url is not None and not self.base_url.startswith(
            ("http://", "https://")
        ):
            raise ConfigurationError(
                f"door {self.name!r}: base_url {self.base_url!r} must start with "
                "http:// or https://, or be None for the SDK's own endpoint"
            )
        if self.reasoning_field is not None and not self.reasoning_field.isidentifier():
            raise ConfigurationError(
                f"door {self.name!r}: reasoning_field {self.reasoning_field!r} must "
                "be a response field name such as 'reasoning_content'"
            )


@dataclass(frozen=True, slots=True)
class RegisteredModel:
    """A model registered through a door — `Model`'s structural twin.

    `value` is the wire id, as on the enum; the properties mirror
    `Model`'s so fallback gates, the context policy, pricing and the
    clients read either without knowing which they hold.
    """

    value: str
    spec: ModelSpec
    door: OpenAICompatible

    @property
    def provider(self) -> Provider:
        return self.spec.provider

    @property
    def max_output_tokens(self) -> int:
        return self.spec.max_output_tokens

    @property
    def context_window(self) -> int:
        return self.spec.context_window

    @property
    def supports_reasoning(self) -> bool:
        return self.spec.supports_reasoning

    @property
    def supports_images(self) -> bool:
        return self.spec.supports_images

    @property
    def supports_documents(self) -> bool:
        return self.spec.supports_documents

    @property
    def supports_max_effort(self) -> bool:
        return self.spec.supports_max_effort

    @property
    def supports_compaction_blocks(self) -> bool:
        return self.spec.supports_compaction_blocks

    @property
    def pricing(self) -> ModelPricing | None:
        return self.spec.pricing


type AnyModel = Model | RegisteredModel

_REGISTRY: dict[str, RegisteredModel] = {}


def _shipped(value: str) -> Model | None:
    try:
        return Model(value)
    except ValueError:
        return None


def register_model(
    value: str,
    *,
    provider: OpenAICompatible,
    context_window: int,
    max_output_tokens: int,
    pricing: ModelPricing | None = None,
    supports_reasoning: bool = False,
    supports_max_effort: bool = False,
) -> RegisteredModel:
    """Register a model served through an OpenAI-compatible door.

    Write-once per id: an identical re-registration returns the existing
    model (module re-imports are safe); a different definition under the
    same id, or an id the `Model` enum already ships, is refused. Media
    capabilities are not offered — the door's converter is text-only,
    and `ModelSpec` describes what neosian's converter implements.
    Registered pricing is the caller's number: it prices runs in µ$ but
    never enters `PRICES_FINGERPRINT`, which seals the shipped rate card.
    """
    if not value or value != value.strip():
        raise ConfigurationError(
            "model id must be a non-empty string without surrounding whitespace"
        )
    if context_window < 1 or max_output_tokens < 1:
        raise ConfigurationError(
            f"model {value!r}: context_window and max_output_tokens must be positive"
        )
    shipped = _shipped(value)
    if shipped is not None:
        raise ConfigurationError(
            f"model {value!r} ships as Model.{shipped.name}; use the enum member"
        )
    spec = ModelSpec(
        provider=Provider.OPENAI_COMPATIBLE,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        supports_reasoning=supports_reasoning,
        supports_max_effort=supports_max_effort,
        pricing=pricing,
    )
    return _register(RegisteredModel(value=value, spec=spec, door=provider))


def _register(model: RegisteredModel) -> RegisteredModel:
    """Write-once: the new row, the identical existing one, or a refusal.

    The catalog's shipped rows (§19.5) enter here with their sealed spec.
    """
    existing = _REGISTRY.get(model.value)
    if existing is None:
        _REGISTRY[model.value] = model
        return model
    if existing == model:
        return existing
    raise ConfigurationError(
        f"model {model.value!r} is already registered with a different "
        "definition; register each id once, or pick another id"
    )


def lookup_model(value: str) -> AnyModel | None:
    """The shipped or registered model with this wire id, if any."""
    shipped = _shipped(value)
    return shipped if shipped is not None else _REGISTRY.get(value)


def registered_models() -> tuple[RegisteredModel, ...]:
    """Every registered model, in registration order."""
    return tuple(_REGISTRY.values())


def provider_label(model: AnyModel) -> str:
    """The provider string rendered for a model: the enum value, or the
    door's name — never the door's shared enum row."""
    if isinstance(model, RegisteredModel):
        return model.door.name
    return model.provider.value
