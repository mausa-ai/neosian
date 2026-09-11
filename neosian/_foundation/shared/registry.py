"""The open model surface (DESIGN §19, §31).

A `RegisteredModel` is `Model`'s structural twin — the same capability
properties over a `ModelSpec` — so every consumer that reads a model
reads both; its door is the spec's, as a shipped door row's is. The
registry is configuration, not run state: process-global, string-keyed,
write-once per id. `resolve_model` is the one boundary where a wire id
becomes the object every internal seam keeps.
"""

from __future__ import annotations

from dataclasses import dataclass

from neosian._foundation.shared.catalog import OpenAICompatible
from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.exceptions import ConfigurationError, InvalidModelError
from neosian._foundation.shared.models import Model, ModelPricing, ModelSpec, Provider


@dataclass(frozen=True, slots=True)
class RegisteredModel:
    """A model registered through a door — `Model`'s structural twin.

    `value` is the wire id, as on the enum; the properties mirror
    `Model`'s so fallback gates, the context policy, pricing and the
    clients read either without knowing which they hold.
    """

    value: str
    spec: ModelSpec

    @property
    def door(self) -> OpenAICompatible:
        assert self.spec.door is not None  # register_model always sets it
        return self.spec.door

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
        door=provider,
    )
    return _register(RegisteredModel(value=value, spec=spec))


def _register(model: RegisteredModel) -> RegisteredModel:
    """Write-once: the new row, the identical existing one, or a refusal."""
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


def resolve_model(value: AnyModel | str) -> AnyModel:
    """The model object for a config's `model` — the object itself, or the
    shipped/registered model a wire id names (§31). An unknown id, or any
    other type, raises `InvalidModelError` listing every known id."""
    # The object check comes first: `Model` is a `str` subclass.
    if isinstance(value, (Model, RegisteredModel)):
        return value
    model = lookup_model(value) if isinstance(value, str) else None
    if model is not None:
        return model
    supported = ", ".join(
        [f"Model.{m.name} ({m.value!r})" for m in Model]
        + [repr(m.value) for m in registered_models()]
    )
    raise InvalidModelError(
        ErrorMessages.INVALID_MODEL.format(
            model_type=type(value).__name__,
            model_value=value,
            supported_models=supported,
        ),
        value,
    )


def registered_models() -> tuple[RegisteredModel, ...]:
    """Every registered model, in registration order."""
    return tuple(_REGISTRY.values())


def provider_label(model: AnyModel) -> str:
    """The provider string rendered for a model: the door's name where
    there is one, else the enum value — never the door's shared enum row."""
    return model.door.name if model.door is not None else model.provider.value
