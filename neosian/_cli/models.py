"""The model picker (`playground --menu`, DESIGN §14.6), derived from the
Model registry."""

from rich.console import Console

from neosian._cli.ui import pick
from neosian._foundation.shared.registry import provider_label, registered_models
from neosian._foundation.shared.types import DEFAULT_MODELS, AnyModel, Model, Provider

# Optional flavor text appended to a model's display name. The "(default)"
# marker is derived from DEFAULT_MODELS, not baked in here.
_MODEL_NOTES: dict[Model, str] = {
    Model.GPT_6_ASTRA: "flagship",
    Model.GPT_5_6_SOL: "most capable 5.6",
    Model.GPT_5_6_TERRA: "balanced",
    Model.GPT_5_6_LUNA: "fastest",
    Model.GPT_5_1: "previous flagship",
    Model.CLAUDE_FABLE_5_1: "most capable",
    Model.CLAUDE_OPUS_5: "capable",
    Model.CLAUDE_SONNET_5: "balanced",
    Model.CEREBRAS_GPT_OSS_120B: "fastest 120B",
    Model.CEREBRAS_QWEN_3_8_27B: "reasoning 27B",
}


def display_name(model: AnyModel) -> str:
    """Build the picker label for a model from the registry."""
    # A door row on the shared enum row is labelled under its door; an
    # adapter row served through a door (Cerebras, #218) keeps its notes.
    if not isinstance(model, Model) or model.provider is Provider.OPENAI_COMPATIBLE:
        return f"{provider_label(model)}/{model.value}"
    notes: list[str] = []
    if DEFAULT_MODELS.get(model.provider) is model:
        notes.append("default")
    note = _MODEL_NOTES.get(model)
    if note is not None:
        notes.append(note)
    return f"{model.value} ({', '.join(notes)})" if notes else model.value


def get_models_for_provider(
    provider: Provider, *, require_reasoning: bool = False
) -> list[tuple[AnyModel, str]]:
    """Get available models for a provider, derived from the Model registry.

    Door rows (DESIGN §19, §31) sit under `Provider.OPENAI_COMPATIBLE`,
    labeled by their door: the shipped ones first, then registrations in
    registration order.

    Args:
        provider: The LLM provider.
        require_reasoning: If True, only return models that support reasoning.

    Returns:
        List of (model, display_name) tuples, default model first.
    """
    models: list[AnyModel] = [
        m
        for m in Model
        if m.provider is provider and (not require_reasoning or m.supports_reasoning)
    ]
    if provider is Provider.OPENAI_COMPATIBLE:
        models += [
            m
            for m in registered_models()
            if not require_reasoning or m.supports_reasoning
        ]
    default = DEFAULT_MODELS.get(provider)
    models.sort(key=lambda m: m is not default)  # stable: default first
    return [(m, display_name(m)) for m in models]


_ALL_PROVIDERS: list[tuple[Provider, str]] = [
    (Provider.OPENAI, "OpenAI"),
    (Provider.ANTHROPIC, "Anthropic (Claude)"),
    (Provider.CEREBRAS, "Cerebras (fast open models)"),
    (Provider.OPENAI_COMPATIBLE, "Registered (OpenAI-compatible doors)"),
]


def get_available_providers(
    *,
    require_reasoning: bool = False,
) -> list[tuple[Provider, str]]:
    """Get providers that have at least one available model.

    Args:
        require_reasoning: If True, only include providers with reasoning models.

    Returns:
        Filtered list of (Provider, display_name) tuples.
    """
    return [
        (p, label)
        for p, label in _ALL_PROVIDERS
        if get_models_for_provider(p, require_reasoning=require_reasoning)
    ]


def select_provider_and_model(
    console: Console, *, require_reasoning: bool = False
) -> AnyModel | None:
    """Show interactive menu to select provider and model.

    Args:
        console: Rich console for output.
        require_reasoning: If True, only show models that support reasoning.

    Returns:
        Selected Model or None if cancelled.
    """
    providers = get_available_providers(require_reasoning=require_reasoning)
    if not providers:
        return None

    while True:
        provider_choice = pick(console, "Select Provider:", [p[1] for p in providers])
        if provider_choice is None:
            return None

        selected_provider = providers[provider_choice][0]

        # Model selection based on provider
        models = get_models_for_provider(
            selected_provider, require_reasoning=require_reasoning
        )
        if not models:
            return None

        model_names = [m[1] for m in models] + ["← Back"]
        model_choice = pick(
            console, f"Select Model ({selected_provider.value}):", model_names
        )
        if model_choice is None or model_choice == len(models):
            # Back to provider selection
            continue

        selected_model: AnyModel = models[model_choice][0]
        return selected_model
