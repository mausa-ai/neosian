"""Model and provider pickers for the CLI, derived from the Model registry."""

from rich.console import Console

from neosian._foundation.shared.constants import ArenaUI
from neosian._foundation.shared.registry import registered_models
from neosian._foundation.shared.types import DEFAULT_MODELS, AnyModel, Model, Provider

# Models hidden from the interactive picker (special-purpose).
_HIDDEN_MODELS: frozenset[Model] = frozenset()

# Optional flavor text appended to a model's display name. The "(default)"
# marker is derived from DEFAULT_MODELS, not baked in here.
_MODEL_NOTES: dict[Model, str] = {
    Model.GPT_5_6_SOL: "most capable",
    Model.GPT_5_6_TERRA: "balanced",
    Model.GPT_5_6_LUNA: "fastest",
    Model.GPT_5_1: "previous flagship",
    Model.CLAUDE_FABLE_5_1: "most capable",
    Model.CLAUDE_OPUS_5: "capable",
    Model.CLAUDE_SONNET_5: "balanced",
    Model.CLAUDE_HAIKU_4_5: "fastest",
    Model.CEREBRAS_GPT_OSS_120B: "fastest 120B",
    Model.CEREBRAS_QWEN_3_8_27B: "reasoning 27B",
}


def display_name(model: AnyModel) -> str:
    """Build the picker label for a model from the registry."""
    if model.door is not None:
        return f"{model.door.name}/{model.value}"
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
        if m.provider is provider
        and m not in _HIDDEN_MODELS
        and (not require_reasoning or m.supports_reasoning)
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

    from simple_term_menu import TerminalMenu  # type: ignore[import-untyped]

    while True:
        console.print("\n[bold]Select Provider:[/bold]")
        provider_menu = TerminalMenu(
            [p[1] for p in providers],
            cursor_index=0,
        )
        provider_choice = provider_menu.show()

        if provider_choice is None:
            return None

        selected_provider = providers[provider_choice][0]

        # Model selection based on provider
        models = get_models_for_provider(
            selected_provider, require_reasoning=require_reasoning
        )
        if not models:
            return None

        console.print(f"\n[bold]Select Model ({selected_provider.value}):[/bold]")
        model_names = [m[1] for m in models] + ["← Back"]
        model_menu = TerminalMenu(model_names, cursor_index=0)
        model_choice = model_menu.show()

        if model_choice is None or model_choice == len(models):
            # Back to provider selection
            continue

        selected_model: AnyModel = models[model_choice][0]
        return selected_model


def select_provider_and_model_labeled(
    console: Console, label: str, *, require_reasoning: bool = False
) -> AnyModel | None:
    """Show interactive menu to select provider and model with a label.

    Args:
        console: Rich console for output.
        label: Label to show (e.g., "Model 1").
        require_reasoning: If True, only show models that support reasoning.

    Returns:
        Selected Model or None if cancelled.
    """
    providers = get_available_providers(require_reasoning=require_reasoning)
    if not providers:
        return None

    from simple_term_menu import TerminalMenu

    while True:
        console.print(f"\n[bold]{ArenaUI.SELECT_PROVIDER.format(label=label)}[/bold]")
        provider_menu = TerminalMenu([p[1] for p in providers], cursor_index=0)
        provider_choice = provider_menu.show()

        if provider_choice is None:
            return None

        selected_provider = providers[provider_choice][0]

        models = get_models_for_provider(
            selected_provider, require_reasoning=require_reasoning
        )
        if not models:
            return None

        console.print(
            f"\n[bold]{ArenaUI.SELECT_MODEL.format(label=label, provider=selected_provider.value)}[/bold]"
        )
        model_names = [m[1] for m in models] + ["← Back"]
        model_menu = TerminalMenu(model_names, cursor_index=0)
        model_choice = model_menu.show()

        if model_choice is None or model_choice == len(models):
            # Back to provider selection
            continue

        labeled_model: AnyModel = models[model_choice][0]
        return labeled_model


def select_arena_models(
    console: Console, *, require_reasoning: bool = False
) -> list[AnyModel] | None:
    """Select models for arena mode.

    Args:
        console: Rich console for output.
        require_reasoning: If True, only show models that support reasoning.

    Returns:
        List of Model enums or None if cancelled.
    """
    # Select count
    from simple_term_menu import TerminalMenu

    console.print(f"\n[bold]{ArenaUI.SELECT_COUNT}[/bold]")
    count_menu = TerminalMenu(list(ArenaUI.COUNT_OPTIONS), cursor_index=0)
    count_choice = count_menu.show()

    if count_choice is None:
        return None

    model_count = int(ArenaUI.COUNT_OPTIONS[count_choice])

    # Select each model
    selections: list[AnyModel] = []
    for i in range(model_count):
        label = ArenaUI.MODEL_LABEL.format(n=i + 1)
        selection = select_provider_and_model_labeled(
            console, label, require_reasoning=require_reasoning
        )
        if selection is None:
            return None
        selections.append(selection)

    return selections
