"""Tests for guardrail type definitions."""

import pytest

from neosian._foundation.shared.types import (
    ClassifierResult,
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
        assert GuardrailMode.CLASSIFIER_ONLY.value == "classifier_only"
        assert GuardrailMode.POLICY_ONLY.value == "policy_only"
        assert GuardrailMode.CLASSIFIER_AND_POLICY.value == "classifier_and_policy"
        assert GuardrailMode.CLASSIFIER_THEN_POLICY.value == "classifier_then_policy"

    def test_uses_classifier_true(self) -> None:
        """Modes that use classifier should return True."""
        assert GuardrailMode.CLASSIFIER_ONLY.uses_classifier() is True
        assert GuardrailMode.CLASSIFIER_AND_POLICY.uses_classifier() is True
        assert GuardrailMode.CLASSIFIER_THEN_POLICY.uses_classifier() is True

    def test_uses_classifier_false(self) -> None:
        """Modes that don't use classifier should return False."""
        assert GuardrailMode.NONE.uses_classifier() is False
        assert GuardrailMode.POLICY_ONLY.uses_classifier() is False

    def test_uses_policy_true(self) -> None:
        """Modes that use policy should return True."""
        assert GuardrailMode.POLICY_ONLY.uses_policy() is True
        assert GuardrailMode.CLASSIFIER_AND_POLICY.uses_policy() is True
        assert GuardrailMode.CLASSIFIER_THEN_POLICY.uses_policy() is True

    def test_uses_policy_false(self) -> None:
        """Modes that don't use policy should return False."""
        assert GuardrailMode.NONE.uses_policy() is False
        assert GuardrailMode.CLASSIFIER_ONLY.uses_policy() is False


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

    def test_classifier_only_no_policy_required(self) -> None:
        """CLASSIFIER_ONLY mode should not require policy."""
        config = GuardrailsConfig(
            input_mode=GuardrailMode.CLASSIFIER_ONLY,
        )
        assert config.input_mode == GuardrailMode.CLASSIFIER_ONLY
        assert config.input_policy is None

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

    def test_classifier_and_policy_requires_policy(self) -> None:
        """CLASSIFIER_AND_POLICY mode should require input_policy."""
        with pytest.raises(ValueError, match="input_policy"):
            GuardrailsConfig(
                input_mode=GuardrailMode.CLASSIFIER_AND_POLICY,
            )

    def test_classifier_then_policy_requires_policy(self) -> None:
        """CLASSIFIER_THEN_POLICY mode should require input_policy."""
        with pytest.raises(ValueError, match="input_policy"):
            GuardrailsConfig(
                input_mode=GuardrailMode.CLASSIFIER_THEN_POLICY,
            )

    def test_output_policy_only_requires_policy(self) -> None:
        """Output POLICY_ONLY mode should require output_policy."""
        with pytest.raises(ValueError, match="output_policy"):
            GuardrailsConfig(
                output_mode=GuardrailMode.POLICY_ONLY,
            )

    def test_output_classifier_and_policy_requires_policy(self) -> None:
        """Output CLASSIFIER_AND_POLICY should require output_policy."""
        with pytest.raises(ValueError, match="output_policy"):
            GuardrailsConfig(
                output_mode=GuardrailMode.CLASSIFIER_AND_POLICY,
            )

    def test_full_config_valid(self) -> None:
        """Full config with all modes and policies should be valid."""
        config = GuardrailsConfig(
            input_mode=GuardrailMode.CLASSIFIER_AND_POLICY,
            input_policy="input policy",
            block_on_input=False,
            output_mode=GuardrailMode.POLICY_ONLY,
            output_policy="output policy",
        )
        assert config.input_mode == GuardrailMode.CLASSIFIER_AND_POLICY
        assert config.input_policy == "input policy"
        assert config.block_on_input is False
        assert config.output_mode == GuardrailMode.POLICY_ONLY
        assert config.output_policy == "output policy"

    def test_has_output_guardrails_true(self) -> None:
        """has_output_guardrails should be True when output_mode is not NONE."""
        config = GuardrailsConfig(
            output_mode=GuardrailMode.CLASSIFIER_ONLY,
        )
        assert config.has_output_guardrails is True

    def test_has_output_guardrails_false(self) -> None:
        """has_output_guardrails should be False when output_mode is NONE."""
        config = GuardrailsConfig(
            input_mode=GuardrailMode.CLASSIFIER_ONLY,
            output_mode=GuardrailMode.NONE,
        )
        assert config.has_output_guardrails is False


@pytest.mark.unit
class TestClassifierResult:
    """Test ClassifierResult dataclass."""

    def test_safe_result(self) -> None:
        """Safe result should have no categories."""
        result = ClassifierResult(safe=True, categories=[])
        assert result.safe is True
        assert result.categories == []

    def test_unsafe_result_with_categories(self) -> None:
        """Unsafe result should have categories."""
        result = ClassifierResult(safe=False, categories=["S1", "S5"])
        assert result.safe is False
        assert result.categories == ["S1", "S5"]

    def test_default_categories_is_empty_list(self) -> None:
        """Default categories should be empty list."""
        result = ClassifierResult(safe=True)
        assert result.categories == []


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

    def test_safe_result_no_blocking(self) -> None:
        """Safe result should have blocked_at as None."""
        result = GuardrailResult(safe=True, blocked_at=None)
        assert result.safe is True
        assert result.blocked_at is None

    def test_input_blocked(self) -> None:
        """Input blocked should have blocked_at='input'."""
        result = GuardrailResult(
            safe=False,
            blocked_at="input",
            input_classifier=ClassifierResult(safe=False, categories=["S2"]),
        )
        assert result.safe is False
        assert result.blocked_at == "input"

    def test_output_blocked(self) -> None:
        """Output blocked should have blocked_at='output'."""
        result = GuardrailResult(
            safe=False,
            blocked_at="output",
            output_policy=PolicyResult(safe=False, category="P3"),
        )
        assert result.safe is False
        assert result.blocked_at == "output"

    def test_flagged_categories_input(self) -> None:
        """flagged_categories should return input classifier categories."""
        result = GuardrailResult(
            safe=False,
            blocked_at="input",
            input_classifier=ClassifierResult(safe=False, categories=["S1", "S3"]),
        )
        assert result.flagged_categories == ["S1", "S3"]

    def test_flagged_categories_output(self) -> None:
        """flagged_categories should return output classifier categories."""
        result = GuardrailResult(
            safe=False,
            blocked_at="output",
            output_classifier=ClassifierResult(safe=False, categories=["S5"]),
        )
        assert result.flagged_categories == ["S5"]

    def test_flagged_categories_combined(self) -> None:
        """flagged_categories should combine input and output categories."""
        result = GuardrailResult(
            safe=False,
            blocked_at="input",
            input_classifier=ClassifierResult(safe=False, categories=["S1"]),
            output_classifier=ClassifierResult(safe=False, categories=["S2"]),
        )
        assert result.flagged_categories == ["S1", "S2"]

    def test_flagged_categories_empty(self) -> None:
        """flagged_categories should be empty when no classifiers."""
        result = GuardrailResult(safe=True)
        assert result.flagged_categories == []

    def test_policy_rationale_input(self) -> None:
        """policy_rationale should return input policy rationale first."""
        result = GuardrailResult(
            safe=False,
            blocked_at="input",
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
            blocked_at="output",
            output_policy=PolicyResult(
                safe=False, category="P2", rationale="Output rationale"
            ),
        )
        assert result.policy_rationale == "Output rationale"

    def test_policy_rationale_none(self) -> None:
        """policy_rationale should be None when no policies."""
        result = GuardrailResult(safe=True)
        assert result.policy_rationale is None
