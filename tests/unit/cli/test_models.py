"""Unit tests for the playground's model picker (`--menu`, DESIGN §14.6)."""

import pytest
from rich.console import Console

import neosian._cli.models as models
from neosian._cli.models import (
    display_name,
    get_models_for_provider,
    select_provider_and_model,
)
from neosian._foundation.shared.types import DEFAULT_MODELS, Model, Provider


@pytest.mark.unit
class TestPlaygroundModelPicker:
    """The picker is derived from the Model registry and cannot drift."""

    def test_every_registry_model_appears_in_picker(self) -> None:
        """Every Model member appears in its provider's picker."""
        for model in Model:
            picker_models = [m for m, _ in get_models_for_provider(model.provider)]
            assert model in picker_models, f"{model} missing from picker"

    def test_default_model_listed_first_with_marker(self) -> None:
        """Each provider's default model is first and labeled (default...)."""
        for provider, default in DEFAULT_MODELS.items():
            models = get_models_for_provider(provider)
            assert models, f"no picker models for {provider}"
            first_model, first_label = models[0]
            assert first_model is default
            assert "default" in first_label

    def test_require_reasoning_filters(self) -> None:
        """require_reasoning=True only returns reasoning-capable models."""
        for provider in Provider:
            for model, _ in get_models_for_provider(provider, require_reasoning=True):
                assert model.supports_reasoning

    def test_display_name_contains_model_id(self) -> None:
        """Labels always start with the exact model ID — a row on the shared
        enum row under its door (DESIGN §19.2); an adapter row served through
        a door (Cerebras, #218) keeps its provider's notes."""
        for model in Model:
            head = model.value
            if model.provider is Provider.OPENAI_COMPATIBLE:
                assert model.door is not None
                head = f"{model.door.name}/"
            assert display_name(model).startswith(head)


def _answers(
    monkeypatch: pytest.MonkeyPatch, *answers: int | None
) -> list[tuple[str, list[str]]]:
    """Script the numbered menu: each `pick` takes the next answer."""
    shown: list[tuple[str, list[str]]] = []
    queue = iter(answers)

    def pick(
        _console: Console, title: str, options: list[str], **_: object
    ) -> int | None:
        shown.append((title, options))
        return next(queue)

    monkeypatch.setattr(models, "pick", pick)
    return shown


@pytest.mark.unit
class TestTheWalk:
    def test_a_provider_then_its_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        shown = _answers(monkeypatch, 0, 0)
        assert select_provider_and_model(Console()) is DEFAULT_MODELS[Provider.OPENAI]
        assert [title for title, _ in shown] == [
            "Select Provider:",
            "Select Model (openai):",
        ]
        assert shown[1][1][-1] == "← Back"

    def test_back_returns_to_the_providers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        back = len(get_models_for_provider(Provider.OPENAI))
        _answers(monkeypatch, 0, back, 1, 0)
        chosen = select_provider_and_model(Console())
        assert chosen is DEFAULT_MODELS[Provider.ANTHROPIC]

    @pytest.mark.parametrize("answers", [(None,), (0, None, None)])
    def test_a_cancel_at_either_menu_picks_nothing(
        self, monkeypatch: pytest.MonkeyPatch, answers: tuple[int | None, ...]
    ) -> None:
        _answers(monkeypatch, *answers)
        assert select_provider_and_model(Console()) is None

    def test_require_reasoning_narrows_the_models_offered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        shown = _answers(monkeypatch, 0, 0)
        chosen = select_provider_and_model(Console(), require_reasoning=True)
        assert chosen is not None and chosen.supports_reasoning
        offered = shown[1][1][:-1]
        assert offered == [
            label
            for _, label in get_models_for_provider(
                Provider.OPENAI, require_reasoning=True
            )
        ]
