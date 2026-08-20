"""Tests for type definitions."""

from typing import cast

import pytest

from neosian._foundation.shared.exceptions import (
    InvalidModelError,
    UnsupportedParameterError,
)
from neosian._foundation.shared.types import (
    AgentConfig,
    AgentName,
    Model,
    ModelSpec,
    Provider,
    ReasoningEffort,
    SystemPrompt,
    ToolName,
)


@pytest.mark.unit
class TestTypeDefinitions:
    """Test that NewType definitions work correctly."""

    def test_newtypes_are_str_compatible(self) -> None:
        """NewTypes should be string-compatible for runtime use."""
        agent = AgentName("test-agent")
        tool = ToolName("my_tool")

        # Should work as strings
        assert agent.startswith("test")
        assert "_" in tool


@pytest.mark.unit
class TestProviderEnum:
    """Test Provider enum."""

    def test_provider_values(self) -> None:
        """Provider enum should have expected values."""
        assert Provider.GROQ.value == "groq"
        assert Provider.OPENAI.value == "openai"
        assert Provider.ANTHROPIC.value == "anthropic"

    def test_provider_is_string_compatible(self) -> None:
        """Provider should be string-compatible."""
        # Direct comparison works due to str, Enum inheritance
        assert cast(str, Provider.GROQ) == "groq"
        # Can be used in string operations
        assert f"provider: {Provider.GROQ.value}" == "provider: groq"


@pytest.mark.unit
class TestModelEnum:
    """Test Model enum."""

    def test_model_values(self) -> None:
        """Model enum should have expected values."""
        assert Model.GROQ_GPT_OSS_20B.value == "openai/gpt-oss-20b"
        assert Model.GROQ_QWEN3_6_27B.value == "qwen/qwen3.6-27b"
        assert Model.GPT_5_NANO.value == "gpt-5-nano-2025-08-07"
        assert Model.CLAUDE_SONNET_5.value == "claude-sonnet-5"

    def test_model_is_string_compatible(self) -> None:
        """Model should be string-compatible."""
        # Direct comparison works due to str, Enum inheritance
        assert cast(str, Model.GROQ_GPT_OSS_20B) == "openai/gpt-oss-20b"
        # Can be used in string operations
        assert f"model: {Model.GROQ_GPT_OSS_20B.value}" == "model: openai/gpt-oss-20b"

    def test_model_provider_property(self) -> None:
        """Model should have provider property."""
        assert Model.GROQ_GPT_OSS_20B.provider == Provider.GROQ
        assert Model.GROQ_QWEN3_6_27B.provider == Provider.GROQ
        assert Model.GPT_5_NANO.provider == Provider.OPENAI
        assert Model.CLAUDE_SONNET_5.provider == Provider.ANTHROPIC

    def test_model_max_output_tokens_property(self) -> None:
        """Model should have max_output_tokens property matching API ceilings."""
        # Groq
        assert Model.GROQ_GPT_OSS_20B.max_output_tokens == 65_536
        assert Model.GROQ_GPT_OSS_120B.max_output_tokens == 65_536
        assert Model.GROQ_QWEN3_6_27B.max_output_tokens == 32_768

        # OpenAI
        assert Model.GPT_5_NANO.max_output_tokens == 128_000
        assert Model.GPT_5_PRO.max_output_tokens == 128_000

        # Anthropic
        assert Model.CLAUDE_OPUS_5.max_output_tokens == 128_000
        assert Model.CLAUDE_OPUS_4_6.max_output_tokens == 128_000
        assert Model.CLAUDE_SONNET_5.max_output_tokens == 128_000
        assert Model.CLAUDE_HAIKU_4_5.max_output_tokens == 64_000

    def test_model_spec_property(self) -> None:
        """Model.spec should return the ModelSpec for that model."""
        spec = Model.GROQ_GPT_OSS_20B.spec
        assert isinstance(spec, ModelSpec)
        assert spec.provider == Provider.GROQ
        assert spec.context_window == 131_072
        assert spec.max_output_tokens == 65_536
        assert spec.supports_reasoning is True

    def test_model_spec_is_frozen(self) -> None:
        """ModelSpec should be immutable."""
        spec = Model.GROQ_GPT_OSS_20B.spec
        with pytest.raises(AttributeError):
            spec.max_output_tokens = 999  # type: ignore[misc]

    def test_model_context_window_property(self) -> None:
        """Model should have context_window property."""
        # Groq: 131,072
        assert Model.GROQ_GPT_OSS_20B.context_window == 131_072
        assert Model.GROQ_QWEN3_6_27B.context_window == 131_072

        # OpenAI: 400k
        assert Model.GPT_5_NANO.context_window == 400_000
        assert Model.GPT_5_PRO.context_window == 400_000

        # Anthropic: 1M except Haiku (200k)
        assert Model.CLAUDE_OPUS_5.context_window == 1_000_000
        assert Model.CLAUDE_SONNET_5.context_window == 1_000_000
        assert Model.CLAUDE_OPUS_4_6.context_window == 1_000_000
        assert Model.CLAUDE_HAIKU_4_5.context_window == 200_000


@pytest.mark.unit
class TestAgentConfigValidation:
    """Test AgentConfig model validation."""

    def test_valid_model_accepted(self) -> None:
        """AgentConfig should accept valid Model enum values."""
        config = AgentConfig(
            system_prompt=SystemPrompt("You are helpful."),
            model=Model.GROQ_GPT_OSS_20B,
        )
        assert config.model == Model.GROQ_GPT_OSS_20B

    def test_default_model(self) -> None:
        """AgentConfig should use default model when not specified."""
        config = AgentConfig(
            system_prompt=SystemPrompt("You are helpful."),
        )
        assert config.model == Model.GROQ_GPT_OSS_20B

    def test_invalid_model_string_raises_error(self) -> None:
        """AgentConfig should raise InvalidModelError for string models."""
        with pytest.raises(InvalidModelError) as exc_info:
            AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                model="gpt-4o",  # type: ignore[arg-type]
            )

        assert exc_info.value.model_value == "gpt-4o"
        assert "str" in str(exc_info.value)
        assert "Model.GROQ_GPT_OSS_20B" in str(exc_info.value)

    def test_invalid_model_int_raises_error(self) -> None:
        """AgentConfig should raise InvalidModelError for non-string types."""
        with pytest.raises(InvalidModelError) as exc_info:
            AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                model=123,  # type: ignore[arg-type]
            )

        assert exc_info.value.model_value == 123
        assert "int" in str(exc_info.value)

    def test_error_message_lists_all_models(self) -> None:
        """InvalidModelError should list all supported models."""
        with pytest.raises(InvalidModelError) as exc_info:
            AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                model="invalid",  # type: ignore[arg-type]
            )

        error_msg = str(exc_info.value)
        # Check that all models are listed
        for model in Model:
            assert f"Model.{model.name}" in error_msg


@pytest.mark.unit
class TestAgentConfigReasoningEffort:
    """Test AgentConfig reasoning_effort validation."""

    def test_reasoning_effort_with_gpt_oss_model_accepted(self) -> None:
        """AgentConfig should accept reasoning_effort with GPT-OSS models."""
        config = AgentConfig(
            system_prompt=SystemPrompt("You are helpful."),
            model=Model.GROQ_GPT_OSS_20B,
            reasoning_effort=ReasoningEffort.HIGH,
        )
        assert config.reasoning_effort == ReasoningEffort.HIGH

    def test_reasoning_effort_with_gpt_oss_120b_accepted(self) -> None:
        """AgentConfig should accept reasoning_effort with GPT-OSS-120B."""
        config = AgentConfig(
            system_prompt=SystemPrompt("You are helpful."),
            model=Model.GROQ_GPT_OSS_120B,
            reasoning_effort=ReasoningEffort.MEDIUM,
        )
        assert config.reasoning_effort == ReasoningEffort.MEDIUM

    def test_reasoning_effort_none_accepted_with_any_model(self) -> None:
        """AgentConfig should accept reasoning_effort=None with any model."""
        config = AgentConfig(
            system_prompt=SystemPrompt("You are helpful."),
            model=Model.GROQ_QWEN3_6_27B,
            reasoning_effort=None,
        )
        assert config.reasoning_effort is None

    def test_reasoning_effort_default_is_none(self) -> None:
        """AgentConfig should default reasoning_effort to None."""
        config = AgentConfig(
            system_prompt=SystemPrompt("You are helpful."),
        )
        assert config.reasoning_effort is None

    def test_reasoning_effort_with_non_reasoning_model_raises_error(self) -> None:
        """AgentConfig should raise error for reasoning_effort with non-reasoning models."""
        with pytest.raises(UnsupportedParameterError) as exc_info:
            AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                model=Model.GROQ_QWEN3_6_27B,
                reasoning_effort=ReasoningEffort.HIGH,
            )

        error_msg = str(exc_info.value)
        assert "qwen/qwen3.6-27b" in error_msg

    def test_reasoning_effort_with_openai_model_accepted(self) -> None:
        """AgentConfig should accept reasoning_effort with OpenAI GPT-5 models."""
        config = AgentConfig(
            system_prompt=SystemPrompt("You are helpful."),
            model=Model.GPT_5_NANO,
            reasoning_effort=ReasoningEffort.LOW,
        )
        assert config.reasoning_effort == ReasoningEffort.LOW

    def test_reasoning_effort_with_non_reasoning_anthropic_model_raises_error(
        self,
    ) -> None:
        """AgentConfig should raise error for reasoning_effort with non-reasoning Anthropic models."""
        with pytest.raises(UnsupportedParameterError) as exc_info:
            AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                model=Model.CLAUDE_HAIKU_4_5,
                reasoning_effort=ReasoningEffort.HIGH,
            )

        error_msg = str(exc_info.value)
        assert "claude-haiku-4-5" in error_msg

    def test_reasoning_effort_with_claude_opus_accepted(self) -> None:
        """AgentConfig should accept reasoning_effort with Claude Opus 4.6."""
        config = AgentConfig(
            system_prompt=SystemPrompt("You are helpful."),
            model=Model.CLAUDE_OPUS_4_6,
            reasoning_effort=ReasoningEffort.HIGH,
        )
        assert config.reasoning_effort == ReasoningEffort.HIGH

    def test_reasoning_effort_max_accepted(self) -> None:
        """AgentConfig should accept reasoning_effort MAX with supported models."""
        config = AgentConfig(
            system_prompt=SystemPrompt("You are helpful."),
            model=Model.CLAUDE_OPUS_4_6,
            reasoning_effort=ReasoningEffort.MAX,
        )
        assert config.reasoning_effort == ReasoningEffort.MAX

    def test_error_message_lists_supported_models(self) -> None:
        """Error message should list supported models."""
        with pytest.raises(UnsupportedParameterError) as exc_info:
            AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                model=Model.GROQ_QWEN3_6_27B,
                reasoning_effort=ReasoningEffort.HIGH,
            )

        error_msg = str(exc_info.value)
        assert "Model.GROQ_GPT_OSS_20B" in error_msg
        assert "Model.GROQ_GPT_OSS_120B" in error_msg
        assert "Model.CLAUDE_OPUS_4_6" in error_msg
        assert "Model.GPT_5_1" in error_msg
        assert "Model.GPT_5_MINI" in error_msg
        assert "Model.GPT_5_NANO" in error_msg
        assert "Model.GPT_5_PRO" in error_msg


@pytest.mark.unit
class TestAgentConfigMaxOutputTokens:
    """Test AgentConfig max_output_tokens validation."""

    def test_max_output_tokens_default(self) -> None:
        """AgentConfig should default max_output_tokens from LLMDefaults."""
        from neosian._foundation.shared.constants import LLMDefaults

        config = AgentConfig(
            system_prompt=SystemPrompt("You are helpful."),
        )
        assert config.max_output_tokens == LLMDefaults.MAX_OUTPUT_TOKENS

    def test_max_output_tokens_custom_value(self) -> None:
        """AgentConfig should accept custom max_output_tokens within model limit."""
        config = AgentConfig(
            system_prompt=SystemPrompt("You are helpful."),
            model=Model.GROQ_GPT_OSS_20B,
            max_output_tokens=4096,
        )
        assert config.max_output_tokens == 4096

    def test_max_output_tokens_at_model_limit(self) -> None:
        """AgentConfig should accept max_output_tokens equal to model limit."""
        config = AgentConfig(
            system_prompt=SystemPrompt("You are helpful."),
            model=Model.GROQ_GPT_OSS_20B,
            max_output_tokens=Model.GROQ_GPT_OSS_20B.max_output_tokens,
        )
        assert config.max_output_tokens == 65_536

    def test_max_output_tokens_exceeds_limit_raises_error(self) -> None:
        """AgentConfig should raise error when max_output_tokens exceeds model limit."""
        with pytest.raises(UnsupportedParameterError) as exc_info:
            AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                model=Model.GROQ_GPT_OSS_20B,
                max_output_tokens=100_000,
            )

        error_msg = str(exc_info.value)
        assert "100000" in error_msg
        assert "65536" in error_msg

    def test_max_output_tokens_zero_raises_error(self) -> None:
        """AgentConfig should raise error when max_output_tokens is 0."""
        with pytest.raises(UnsupportedParameterError):
            AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                max_output_tokens=0,
            )

    def test_max_output_tokens_negative_raises_error(self) -> None:
        """AgentConfig should raise error when max_output_tokens is negative."""
        with pytest.raises(UnsupportedParameterError):
            AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                max_output_tokens=-1,
            )


@pytest.mark.unit
class TestFakeModels:
    """The FAKE registry members (DESIGN §2, ECOSYSTEM §7)."""

    def test_specs_resolve(self) -> None:
        for model in (Model.FAKE, Model.FAKE_SMALL, Model.FAKE_REASONING):
            spec = model.spec
            assert spec.provider is Provider.FAKE
            assert model.pricing is not None
            assert model.max_output_tokens == 8_192

    def test_capability_split(self) -> None:
        assert Model.FAKE.supports_images and Model.FAKE.supports_documents
        assert not Model.FAKE_SMALL.supports_images
        assert not Model.FAKE_SMALL.supports_documents
        assert Model.FAKE_REASONING.supports_reasoning
        assert Model.FAKE_REASONING.supports_max_effort
        assert not Model.FAKE.supports_reasoning
        assert Model.FAKE_SMALL.context_window < Model.FAKE.context_window

    def test_default_agent_config_constructs_on_fake_models(self) -> None:
        """max_output_tokens >= the 8192 default — the keyless-boot guard."""
        for model in (Model.FAKE, Model.FAKE_SMALL, Model.FAKE_REASONING):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."), model=model
            )
            assert config.model is model

    def test_reasoning_effort_rejected_on_non_reasoning_fake(self) -> None:
        AgentConfig(
            system_prompt=SystemPrompt("You are helpful."),
            model=Model.FAKE_REASONING,
            reasoning_effort=ReasoningEffort.LOW,
        )
        with pytest.raises(UnsupportedParameterError):
            AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                model=Model.FAKE,
                reasoning_effort=ReasoningEffort.LOW,
            )

    def test_default_models_total_over_providers(self) -> None:
        from neosian._foundation.shared.types import DEFAULT_MODELS

        assert set(DEFAULT_MODELS) == set(Provider)
        assert DEFAULT_MODELS[Provider.FAKE] is Model.FAKE


class TestCompactionCapability:
    def test_support_set(self) -> None:
        """The compact beta's support set — a provider check would be
        wrong: Haiku 4.5 is Anthropic and explicitly outside it."""
        assert Model.CLAUDE_OPUS_5.supports_compaction_blocks
        assert Model.CLAUDE_OPUS_4_6.supports_compaction_blocks
        assert Model.CLAUDE_SONNET_5.supports_compaction_blocks
        assert not Model.CLAUDE_HAIKU_4_5.supports_compaction_blocks

    def test_non_anthropic_models_are_unsupported(self) -> None:
        assert not Model.FAKE.supports_compaction_blocks
        assert not Model.GROQ_GPT_OSS_120B.supports_compaction_blocks
        assert not Model.GPT_5_1.supports_compaction_blocks
