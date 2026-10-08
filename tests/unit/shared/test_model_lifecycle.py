"""Selectors, migration notices and release-time promotion without clock routing."""

from datetime import date, timedelta
from typing import Any

import pytest

from neosian import (
    AgentConfig,
    CompactionConfig,
    ConfigurationError,
    FallbackConfig,
    GuardrailsConfig,
    InvalidModelError,
    Model,
    ModelSelector,
    ModelTransitionWarning,
    OpenAICompatible,
    ReflectionConfig,
    lookup_model,
    register_model,
)
from neosian._foundation.evaluation.schema import parse_models
from neosian._foundation.shared import model_lifecycle as lifecycle
from neosian._foundation.shared.models import DEFAULT_MODELS, Provider
from neosian._foundation.shared.registry import resolve_model

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("selector", list(ModelSelector))
def test_every_selector_is_a_concrete_model(selector: ModelSelector) -> None:
    model = resolve_model(selector)
    assert isinstance(model, Model)
    assert lookup_model(selector.value) is model


@pytest.mark.parametrize(
    "build",
    [
        lambda m: AgentConfig(system_prompt="x", model=m),
        lambda m: FallbackConfig(model=m),
        lambda m: GuardrailsConfig(model=m),
        lambda m: CompactionConfig(model=m),
        lambda m: ReflectionConfig(model=m),
    ],
)
def test_all_configuration_boundaries_resolve_selectors(build: Any) -> None:
    assert build(ModelSelector.GPT_SOL_LATEST).model is Model.GPT_6_1_SOL


def test_provider_shortcuts_keep_the_balanced_tier() -> None:
    assert resolve_model("anthropic:latest") is Model.CLAUDE_SONNET_5_5
    assert resolve_model("anthropic:haiku:latest") is Model.CLAUDE_HAIKU_5_5
    assert lifecycle.DEFAULTS["anthropic:haiku"] is lifecycle.LATEST["anthropic:haiku"]
    assert resolve_model("openai:latest") is Model.GPT_6_1_SOL
    assert resolve_model("openai:default") is DEFAULT_MODELS[Provider.OPENAI]
    assert resolve_model("anthropic:default") is DEFAULT_MODELS[Provider.ANTHROPIC]
    assert DEFAULT_MODELS[Provider.CEREBRAS] is Model.CEREBRAS_GPT_OSS_120B


def test_evaluation_does_not_strip_selector_provider() -> None:
    assert parse_models(["openai:sol:latest"], "suite") == (Model.GPT_6_1_SOL,)
    assert parse_models(["anthropic:claude-sonnet-5-5"], "suite") == (
        Model.CLAUDE_SONNET_5_5,
    )


@pytest.mark.parametrize("selection", [Model.GPT_6_SOL, "gpt-6-sol", "openai:default"])
def test_notice_names_replacement_and_publication_window(
    selection: Model | str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(lifecycle.RELEASE_DATES, "1.4.0", None)
    with pytest.warns(ModelTransitionWarning, match="gpt-6.1-sol") as caught:
        assert resolve_model(selection) is Model.GPT_6_SOL
    notice = str(caught[0].message)
    assert "30 days" in notice and "publication date pending" in notice
    assert "openai:sol:latest" in notice


def test_warning_has_fixed_utc_deadline_after_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(lifecycle.RELEASE_DATES, "1.4.0", date(2026, 10, 10))
    with pytest.warns(ModelTransitionWarning, match="2026-11-09 UTC"):
        resolve_model(Model.GPT_6_SOL)


def test_overlapping_releases_age_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = date(2026, 10, 10)
    monkeypatch.setattr(
        lifecycle, "RELEASE_DATES", {"a": start, "b": start + timedelta(days=10)}
    )
    monkeypatch.setattr(
        lifecycle,
        "TRANSITIONS",
        (
            lifecycle.Transition("old", "a", "a", "openai:sol"),
            lifecycle.Transition("a", "b", "b", "openai:sol"),
        ),
    )
    assert (
        lifecycle.eligible_successor("openai:sol", on=start + timedelta(days=29))
        is None
    )
    assert (
        lifecycle.eligible_successor("openai:sol", on=start + timedelta(days=30)) == "a"
    )
    assert (
        lifecycle.eligible_successor("openai:sol", on=start + timedelta(days=39)) == "a"
    )
    assert (
        lifecycle.eligible_successor("openai:sol", on=start + timedelta(days=40)) == "b"
    )
    # Even a far-future release-tool query cannot mutate runtime routing.
    lifecycle.eligible_successor("openai:sol", on=date(2099, 1, 1))
    assert resolve_model("openai:default") is Model.GPT_6_SOL


def test_release_gate_requires_dates_and_due_promotions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = date(2026, 10, 10)
    monkeypatch.setitem(lifecycle.RELEASE_DATES, "1.4.0", None)
    assert lifecycle.release_errors(version="1.4.0", on=start)
    monkeypatch.setitem(lifecycle.RELEASE_DATES, "1.4.0", start)
    assert lifecycle.release_errors(version="1.4.0", on=start) == []
    assert (
        lifecycle.release_errors(version="1.4.1", on=start + timedelta(days=29)) == []
    )
    errors = lifecycle.release_errors(version="1.5.0", on=start + timedelta(days=30))
    assert any("Retire gpt-6-sol" in e for e in errors)
    assert any("Promote openai:sol:default" in e for e in errors)


def test_retired_pin_is_actionable_and_never_redirected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        lifecycle,
        "TRANSITIONS",
        (lifecycle.Transition("retired-model", "gpt-6.1-sol", "1.4.0", "openai:sol"),),
    )
    assert lookup_model("retired-model") is None
    with pytest.raises(InvalidModelError, match="gpt-6.1-sol"):
        resolve_model("retired-model")
    with pytest.raises(ConfigurationError, match="reserved"):
        register_model(
            "retired-model",
            provider=OpenAICompatible(name="local", api_key_env="KEY"),
            context_window=100,
            max_output_tokens=10,
        )


def test_release_gate_refuses_early_promotion_and_removal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = date(2026, 10, 10)
    monkeypatch.setitem(lifecycle.RELEASE_DATES, "1.4.0", start)
    monkeypatch.setitem(lifecycle.DEFAULTS, "openai:sol", Model.GPT_6_1_SOL)
    monkeypatch.delitem(Model._value2member_map_, "gpt-6-sol")
    errors = lifecycle.release_errors(version="1.4.0", on=start)
    assert any("Keep gpt-6-sol until 2026-11-09" in e for e in errors)
    assert any("openai:sol:default to gpt-6-sol" in e for e in errors)


def test_registration_cannot_shadow_a_selector() -> None:
    with pytest.raises(ConfigurationError, match="reserved"):
        register_model(
            "openai:latest",
            provider=OpenAICompatible(name="local", api_key_env="KEY"),
            context_window=100,
            max_output_tokens=10,
        )
