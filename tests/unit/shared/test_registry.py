"""The open model surface (DESIGN §19): doors, the twin, the registry."""

from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest

from neosian import (
    PRICES_FINGERPRINT,
    ConfigurationError,
    Model,
    ModelPricing,
    OpenAICompatible,
    Provider,
    RegisteredModel,
    Usage,
    register_model,
)
from neosian._foundation.shared.models import _prices_fingerprint
from neosian._foundation.shared.registry import (
    lookup_model,
    provider_label,
    registered_models,
)

XAI = OpenAICompatible(
    name="xai", api_key_env="XAI_API_KEY", base_url="https://api.x.ai/v1"
)
# One µ$ per million tokens each way: a single token rounds up to 1 µ$.
PRICING = ModelPricing(input_per_mtok=1, output_per_mtok=1)


def _grok() -> RegisteredModel:
    return register_model(
        "grok-4",
        provider=XAI,
        context_window=131_072,
        max_output_tokens=16_384,
        pricing=PRICING,
        supports_reasoning=True,
    )


@pytest.mark.unit
class TestDoor:
    def test_defaults_are_openais_dialect(self) -> None:
        door = OpenAICompatible(name="door", api_key_env="DOOR_KEY")
        assert door.base_url is None
        assert door.temperature is False
        assert door.reasoning_effort is True
        assert door.reasoning_field is None
        assert door.strict_schemas is True

    @pytest.mark.parametrize(
        "override",
        [
            {"name": "Bad Name"},
            {"name": ""},
            {"name": "1st"},
            {"api_key_env": "lower_key"},
            {"api_key_env": "X-KEY"},
            {"base_url": "ftp://api.x.ai"},
            {"base_url": "api.x.ai/v1"},
            {"reasoning_field": "not a field"},
        ],
    )
    def test_shape_errors_name_the_field(self, override: dict[str, Any]) -> None:
        kwargs: dict[str, Any] = {"name": "door", "api_key_env": "DOOR_KEY"}
        with pytest.raises(ConfigurationError) as exc_info:
            OpenAICompatible(**{**kwargs, **override})
        assert next(iter(override)) in str(exc_info.value)

    def test_frozen(self) -> None:
        with pytest.raises(FrozenInstanceError):
            XAI.name = "grok"  # type: ignore[misc]


@pytest.mark.unit
class TestRegisteredModel:
    def test_every_model_property_exists_on_the_twin(self) -> None:
        enum_properties = {
            name for name, value in vars(Model).items() if isinstance(value, property)
        }
        grok = _grok()
        for name in enum_properties:
            assert hasattr(grok, name), name
        assert grok.value == "grok-4"

    def test_capabilities_come_from_the_spec(self) -> None:
        grok = _grok()
        assert grok.provider is Provider.OPENAI_COMPATIBLE
        assert grok.door is XAI
        assert grok.context_window == 131_072
        assert grok.max_output_tokens == 16_384
        assert grok.supports_reasoning is True
        assert grok.supports_max_effort is False
        # The door's converter is text-only, and Anthropic-only features stay so.
        assert grok.supports_images is False
        assert grok.supports_documents is False
        assert grok.supports_compaction_blocks is False

    def test_prices_in_micro_usd_with_ceiling(self) -> None:
        grok = _grok()
        assert Usage(input_tokens=1, output_tokens=1).cost_micro_usd(grok) == 1
        unpriced = register_model(
            "grok-free", provider=XAI, context_window=8_192, max_output_tokens=1_024
        )
        assert Usage(input_tokens=1, output_tokens=1).cost_micro_usd(unpriced) is None

    def test_provider_label_is_the_door_name(self) -> None:
        assert provider_label(_grok()) == "xai"
        assert provider_label(Model.FAKE) == "fake"


@pytest.mark.unit
class TestRegistry:
    def test_starts_empty(self) -> None:
        # The shipped door rows are enum members (§31); nothing registers at import.
        assert registered_models() == ()

    def test_register_then_lookup(self) -> None:
        grok = _grok()
        assert lookup_model("grok-4") is grok
        assert registered_models() == (grok,)

    def test_lookup_prefers_the_enum_and_misses_unknown_ids(self) -> None:
        assert lookup_model("fake") is Model.FAKE
        assert lookup_model("grok-4.6") is Model.GROK_4_6
        assert lookup_model("no-such-model") is None

    def test_identical_reregistration_is_idempotent(self) -> None:
        assert _grok() is _grok()
        assert len(registered_models()) == 1

    def test_conflicting_definition_is_refused(self) -> None:
        _grok()
        with pytest.raises(ConfigurationError, match="already registered"):
            register_model(
                "grok-4", provider=XAI, context_window=1, max_output_tokens=1
            )

    def test_shipped_id_is_refused_naming_the_member(self) -> None:
        with pytest.raises(ConfigurationError, match="Model.FAKE"):
            register_model("fake", provider=XAI, context_window=1, max_output_tokens=1)

    @pytest.mark.parametrize(
        ("value", "window", "output"),
        [("", 1, 1), (" grok ", 1, 1), ("grok", 0, 1), ("grok", 1, -1)],
    )
    def test_shape_errors(self, value: str, window: int, output: int) -> None:
        with pytest.raises(ConfigurationError):
            register_model(
                value, provider=XAI, context_window=window, max_output_tokens=output
            )

    def test_registered_pricing_never_enters_the_fingerprint(self) -> None:
        before = _prices_fingerprint()
        _grok()
        assert _prices_fingerprint() == before == PRICES_FINGERPRINT

    def test_registration_order_is_kept(self) -> None:
        a = register_model("a", provider=XAI, context_window=1, max_output_tokens=1)
        b = register_model("b", provider=XAI, context_window=1, max_output_tokens=1)
        assert registered_models() == (a, b)


@pytest.mark.unit
def test_the_door_row_is_a_registry_member() -> None:
    assert Provider.OPENAI_COMPATIBLE.value == "openai_compatible"
    assert Provider.OPENAI_COMPATIBLE in list(Provider)


@pytest.mark.unit
class TestParsersResolveRegisteredIds:
    def test_eval_models_axis(self) -> None:
        from neosian._foundation.evaluation.schema import parse_models

        grok = _grok()
        assert parse_models(["grok-4", "xai:grok-4", "fake"], "p.yaml") == (
            grok,
            grok,
            Model.FAKE,
        )

    def test_memory_cli_model_flag(self) -> None:
        from neosian._foundation.memory.cli_grammar import parse_model
        from neosian._foundation.memory.settings import StreamParser

        class _Sub:
            def error(self, message: str) -> None:
                raise SystemExit(message)

        sub = cast(StreamParser, _Sub())
        grok = _grok()
        assert parse_model(sub, "grok-4") is grok
        with pytest.raises(SystemExit, match="grok-4"):
            parse_model(sub, "no-such-model")
