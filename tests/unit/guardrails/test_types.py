"""Tests for guardrail type definitions."""

import pytest

from neosian._foundation.shared.types import (
    GuardrailMode,
    GuardrailResult,
    GuardrailsConfig,
    PolicyResult,
)


@pytest.mark.unit
class TestGuardrailMode:
    """Test GuardrailMode enum."""

    def test_mode_values(self) -> None:
        """GuardrailMode should have expected string values."""
        assert GuardrailMode.NONE.value == "none"
        assert GuardrailMode.POLICY_ONLY.value == "policy_only"

    def test_only_two_modes(self) -> None:
        """GuardrailMode should only have NONE and POLICY_ONLY."""
        assert len(GuardrailMode) == 2


@pytest.mark.unit
class TestGuardrailsConfig:
    """Test GuardrailsConfig dataclass."""

    def test_default_config(self) -> None:
        """Default config should have no guardrails."""
        config = GuardrailsConfig()
        assert config.input_mode == GuardrailMode.NONE
        assert config.output_mode == GuardrailMode.NONE
        assert config.input_policy is None
        assert config.output_policy is None
        assert config.block_on_input is True

    def test_policy_only_requires_policy(self) -> None:
        """POLICY_ONLY mode should require input_policy."""
        with pytest.raises(ValueError, match="input_policy"):
            GuardrailsConfig(
                input_mode=GuardrailMode.POLICY_ONLY,
            )

    def test_policy_only_with_policy_valid(self) -> None:
        """POLICY_ONLY mode with policy should be valid."""
        config = GuardrailsConfig(
            input_mode=GuardrailMode.POLICY_ONLY,
            input_policy="some policy",
        )
        assert config.input_mode == GuardrailMode.POLICY_ONLY
        assert config.input_policy == "some policy"

    def test_output_policy_only_requires_policy(self) -> None:
        """Output POLICY_ONLY mode should require output_policy."""
        with pytest.raises(ValueError, match="output_policy"):
            GuardrailsConfig(
                output_mode=GuardrailMode.POLICY_ONLY,
            )

    def test_full_config_valid(self) -> None:
        """Full config with input and output policies should be valid."""
        config = GuardrailsConfig(
            input_mode=GuardrailMode.POLICY_ONLY,
            input_policy="input policy",
            block_on_input=False,
            output_mode=GuardrailMode.POLICY_ONLY,
            output_policy="output policy",
        )
        assert config.input_mode == GuardrailMode.POLICY_ONLY
        assert config.input_policy == "input policy"
        assert config.block_on_input is False
        assert config.output_mode == GuardrailMode.POLICY_ONLY
        assert config.output_policy == "output policy"

    def test_has_output_guardrails_true(self) -> None:
        """has_output_guardrails should be True when output_mode is not NONE."""
        config = GuardrailsConfig(
            output_mode=GuardrailMode.POLICY_ONLY,
            output_policy="policy",
        )
        assert config.has_output_guardrails is True

    def test_has_output_guardrails_false(self) -> None:
        """has_output_guardrails should be False when output_mode is NONE."""
        config = GuardrailsConfig(
            input_mode=GuardrailMode.POLICY_ONLY,
            input_policy="policy",
            output_mode=GuardrailMode.NONE,
        )
        assert config.has_output_guardrails is False


@pytest.mark.unit
class TestPolicyResult:
    """Test PolicyResult dataclass."""

    def test_safe_result(self) -> None:
        """Safe policy result should have no category or rationale."""
        result = PolicyResult(safe=True)
        assert result.safe is True
        assert result.category is None
        assert result.rationale is None

    def test_unsafe_result(self) -> None:
        """Unsafe policy result should have category and rationale."""
        result = PolicyResult(
            safe=False,
            category="P1",
            rationale="Prompt injection detected",
        )
        assert result.safe is False
        assert result.category == "P1"
        assert result.rationale == "Prompt injection detected"


@pytest.mark.unit
class TestGuardrailResult:
    """Test GuardrailResult dataclass."""

    def test_safe_result_no_flagging(self) -> None:
        """Safe result should have flagged_at as None."""
        result = GuardrailResult(safe=True, flagged_at=None)
        assert result.safe is True
        assert result.flagged_at is None

    def test_input_flagged(self) -> None:
        """Input flagged should have flagged_at='input'."""
        result = GuardrailResult(
            safe=False,
            flagged_at="input",
            input_policy=PolicyResult(safe=False, category="P1", rationale="Flagged"),
        )
        assert result.safe is False
        assert result.flagged_at == "input"

    def test_output_flagged(self) -> None:
        """Output flagged should have flagged_at='output'."""
        result = GuardrailResult(
            safe=False,
            flagged_at="output",
            output_policy=PolicyResult(safe=False, category="P3"),
        )
        assert result.safe is False
        assert result.flagged_at == "output"

    def test_policy_rationale_input(self) -> None:
        """policy_rationale should return input policy rationale first."""
        result = GuardrailResult(
            safe=False,
            flagged_at="input",
            input_policy=PolicyResult(
                safe=False, category="P1", rationale="Input rationale"
            ),
            output_policy=PolicyResult(
                safe=False, category="P2", rationale="Output rationale"
            ),
        )
        assert result.policy_rationale == "Input rationale"

    def test_policy_rationale_output_fallback(self) -> None:
        """policy_rationale should fallback to output when no input."""
        result = GuardrailResult(
            safe=False,
            flagged_at="output",
            output_policy=PolicyResult(
                safe=False, category="P2", rationale="Output rationale"
            ),
        )
        assert result.policy_rationale == "Output rationale"

    def test_policy_rationale_none(self) -> None:
        """policy_rationale should be None when no policies."""
        result = GuardrailResult(safe=True)
        assert result.policy_rationale is None
