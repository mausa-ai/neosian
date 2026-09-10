"""Strings at the config boundary (DESIGN §31): a wire id resolves once, at
construction, into the object every internal seam keeps."""

from typing import Any

import pytest

from neosian import (
    AgentConfig,
    CompactionConfig,
    FallbackConfig,
    GuardrailsConfig,
    InvalidModelError,
    Model,
    OpenAICompatible,
    ReflectionConfig,
    lookup_model,
    register_model,
)
from neosian._foundation.shared.registry import resolve_model

XAI = OpenAICompatible(name="xai", api_key_env="XAI_API_KEY")


def _agent(model: object) -> Any:
    return AgentConfig(system_prompt="x", model=model)  # type: ignore[arg-type]


_CONFIGS = [
    pytest.param(_agent, id="AgentConfig"),
    pytest.param(FallbackConfig, id="FallbackConfig"),
    pytest.param(lambda m: GuardrailsConfig(model=m), id="GuardrailsConfig"),
    pytest.param(lambda m: CompactionConfig(model=m), id="CompactionConfig"),
    pytest.param(lambda m: ReflectionConfig(model=m), id="ReflectionConfig"),
]


@pytest.mark.unit
class TestStringsAtTheBoundary:
    @pytest.mark.parametrize("build", _CONFIGS)
    def test_a_wire_id_builds_the_same_config_as_the_member(self, build: Any) -> None:
        assert build("gpt-oss-120b") == build(Model.CEREBRAS_GPT_OSS_120B)
        assert build("grok-4.6").model is Model.GROK_4_6

    @pytest.mark.parametrize("build", _CONFIGS)
    def test_a_registered_id_resolves_to_the_registration(self, build: Any) -> None:
        grok = register_model(
            "grok-4", provider=XAI, context_window=131_072, max_output_tokens=16_384
        )
        assert build("grok-4").model is grok

    @pytest.mark.parametrize("build", _CONFIGS)
    def test_an_unknown_id_is_refused_naming_the_known_ones(self, build: Any) -> None:
        with pytest.raises(InvalidModelError, match="Model.GROK_4_6 \\('grok-4.6'\\)"):
            build("no-such-model")

    @pytest.mark.parametrize("build", _CONFIGS[2:])
    def test_none_stays_the_agents_own_model(self, build: Any) -> None:
        assert build(None).model is None

    def test_resolve_model_is_the_lookup_that_raises(self) -> None:
        assert resolve_model(Model.FAKE) is Model.FAKE
        assert resolve_model("fake") is lookup_model("fake") is Model.FAKE
        with pytest.raises(InvalidModelError):
            resolve_model(123)  # type: ignore[arg-type]
