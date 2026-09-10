"""Unit tests for the playground model picker derivation."""

import pytest

from neosian._cli.models import (
    _HIDDEN_MODELS,
    display_name,
    get_models_for_provider,
)
from neosian._foundation.shared.types import DEFAULT_MODELS, Model, Provider


@pytest.mark.unit
class TestPlaygroundModelPicker:
    """The picker is derived from the Model registry and cannot drift."""

    def test_every_registry_model_appears_in_picker(self) -> None:
        """Every non-hidden Model member must appear in its provider's picker."""
        for model in Model:
            if model in _HIDDEN_MODELS:
                continue
            picker_models = [m for m, _ in get_models_for_provider(model.provider)]
            assert model in picker_models, f"{model} missing from picker"

    def test_hidden_models_excluded(self) -> None:
        """Hidden models never appear in any provider's picker."""
        for provider in Provider:
            picker_models = [m for m, _ in get_models_for_provider(provider)]
            for hidden in _HIDDEN_MODELS:
                assert hidden not in picker_models

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
        """Labels always start with the exact model ID — a door row's under
        its door (DESIGN §19.2)."""
        for model in Model:
            head = model.value if model.door is None else f"{model.door.name}/"
            assert display_name(model).startswith(head)
