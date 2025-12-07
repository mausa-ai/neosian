"""Tests for policy builder module."""

import pytest

from neosian._foundation.guardrails.policy import (
    CommonPolicies,
    PolicyBuilder,
    PolicyCategory,
    evaluate_test_policy,
    is_test_policy,
)
from neosian._foundation.shared.constants import Guardrails


@pytest.mark.unit
class TestPolicyCategory:
    """Test PolicyCategory dataclass."""

    def test_basic_category(self) -> None:
        """Create basic policy category."""
        category = PolicyCategory(
            name="Test Policy",
            code="P1",
            description="A test policy",
        )
        assert category.name == "Test Policy"
        assert category.code == "P1"
        assert category.description == "A test policy"
        assert category.violates == []
        assert category.safe == []

    def test_category_with_examples(self) -> None:
        """Create category with violation and safe examples."""
        category = PolicyCategory(
            name="Test Policy",
            code="P1",
            description="A test policy",
            violates=["bad content"],
            safe=["good content"],
        )
        assert category.violates == ["bad content"]
        assert category.safe == ["good content"]

    def test_to_prompt_basic(self) -> None:
        """to_prompt should generate formatted string."""
        category = PolicyCategory(
            name="Test Policy",
            code="P1",
            description="A test policy",
        )
        prompt = category.to_prompt()
        assert "P1" in prompt
        assert "Test Policy" in prompt
        assert "A test policy" in prompt

    def test_to_prompt_with_examples(self) -> None:
        """to_prompt should include examples."""
        category = PolicyCategory(
            name="Test Policy",
            code="P1",
            description="A test policy",
            violates=["bad1", "bad2"],
            safe=["good1"],
        )
        prompt = category.to_prompt()
        assert '"bad1"' in prompt
        assert '"bad2"' in prompt
        assert '"good1"' in prompt


@pytest.mark.unit
class TestPolicyBuilder:
    """Test PolicyBuilder class."""

    def test_empty_builder_returns_empty_string(self) -> None:
        """Empty builder should return empty string."""
        builder = PolicyBuilder()
        assert builder.build() == ""

    def test_add_predefined_category(self) -> None:
        """Add predefined category."""
        policy = PolicyBuilder().add(CommonPolicies.PROMPT_INJECTION).build()
        assert "P1" in policy
        assert "Prompt Injection" in policy

    def test_add_multiple_categories(self) -> None:
        """Add multiple predefined categories."""
        policy = (
            PolicyBuilder()
            .add(CommonPolicies.PROMPT_INJECTION)
            .add(CommonPolicies.COMPETITOR_MENTIONS)
            .build()
        )
        assert "P1" in policy
        assert "P2" in policy

    def test_add_custom_category(self) -> None:
        """Add custom category."""
        policy = (
            PolicyBuilder()
            .add_custom(
                name="Custom Policy",
                code="P99",
                description="A custom policy",
                violates=["bad"],
                safe=["good"],
            )
            .build()
        )
        assert "P99" in policy
        assert "Custom Policy" in policy
        assert "A custom policy" in policy

    def test_method_chaining(self) -> None:
        """Builder should support method chaining."""
        builder = PolicyBuilder()
        result = builder.add(CommonPolicies.PROMPT_INJECTION)
        assert result is builder

        result = builder.add_custom(
            name="Test",
            code="P99",
            description="Test",
        )
        assert result is builder

    def test_codes_property(self) -> None:
        """codes property should return all category codes."""
        builder = PolicyBuilder()
        builder.add(CommonPolicies.PROMPT_INJECTION)
        builder.add(CommonPolicies.COMPETITOR_MENTIONS)
        builder.add_custom(name="Test", code="P99", description="Test")

        assert builder.codes == ["P1", "P2", "P99"]

    def test_codes_empty_builder(self) -> None:
        """codes should be empty for empty builder."""
        builder = PolicyBuilder()
        assert builder.codes == []

    def test_test_policy_returns_marker(self) -> None:
        """test() should return special test marker."""
        policy = PolicyBuilder.test()
        assert policy == Guardrails.TestPolicy.MARKER

    def test_default_policy_includes_common_policies(self) -> None:
        """default() should include common policies."""
        policy = PolicyBuilder.default()
        # Should include prompt injection, harmful instructions, personal data
        assert "P1" in policy  # PROMPT_INJECTION
        assert "P4" in policy  # HARMFUL_INSTRUCTIONS
        assert "P5" in policy  # PERSONAL_DATA_EXTRACTION


@pytest.mark.unit
class TestCommonPolicies:
    """Test CommonPolicies predefined categories."""

    def test_prompt_injection_policy(self) -> None:
        """PROMPT_INJECTION should be properly defined."""
        policy = CommonPolicies.PROMPT_INJECTION
        assert policy.code == "P1"
        assert policy.name == "Prompt Injection"
        assert len(policy.violates) > 0
        assert len(policy.safe) > 0

    def test_competitor_mentions_policy(self) -> None:
        """COMPETITOR_MENTIONS should be properly defined."""
        policy = CommonPolicies.COMPETITOR_MENTIONS
        assert policy.code == "P2"
        assert policy.name == "Competitor Mentions"

    def test_internal_data_policy(self) -> None:
        """INTERNAL_DATA should be properly defined."""
        policy = CommonPolicies.INTERNAL_DATA
        assert policy.code == "P3"
        assert policy.name == "Internal Data Requests"

    def test_harmful_instructions_policy(self) -> None:
        """HARMFUL_INSTRUCTIONS should be properly defined."""
        policy = CommonPolicies.HARMFUL_INSTRUCTIONS
        assert policy.code == "P4"
        assert policy.name == "Harmful Instructions"

    def test_personal_data_extraction_policy(self) -> None:
        """PERSONAL_DATA_EXTRACTION should be properly defined."""
        policy = CommonPolicies.PERSONAL_DATA_EXTRACTION
        assert policy.code == "P5"
        assert policy.name == "Personal Data Extraction"

    def test_profanity_and_abuse_policy(self) -> None:
        """PROFANITY_AND_ABUSE should be properly defined."""
        policy = CommonPolicies.PROFANITY_AND_ABUSE
        assert policy.code == "P6"
        assert policy.name == "Profanity and Abuse"


@pytest.mark.unit
class TestTestPolicy:
    """Test test policy helpers."""

    def test_is_test_policy_true(self) -> None:
        """is_test_policy should return True for test marker."""
        assert is_test_policy(Guardrails.TestPolicy.MARKER) is True

    def test_is_test_policy_false(self) -> None:
        """is_test_policy should return False for other policies."""
        assert is_test_policy("some regular policy") is False
        assert is_test_policy("") is False
        assert is_test_policy(PolicyBuilder.default()) is False

    def test_evaluate_test_policy_returns_tuple(self) -> None:
        """evaluate_test_policy should return 3-tuple."""
        result = evaluate_test_policy()
        assert isinstance(result, tuple)
        assert len(result) == 3
        safe, category, rationale = result
        assert isinstance(safe, bool)

    def test_evaluate_test_policy_safe_result(self) -> None:
        """Safe result from test policy should have None category/rationale."""
        # Run multiple times to get a safe result
        for _ in range(100):
            safe, category, rationale = evaluate_test_policy()
            if safe:
                assert category is None
                assert rationale is None
                return
        pytest.fail("Never got a safe result in 100 tries")

    def test_evaluate_test_policy_unsafe_result(self) -> None:
        """Unsafe result from test policy should have test values."""
        # Run multiple times to get an unsafe result
        for _ in range(100):
            safe, category, rationale = evaluate_test_policy()
            if not safe:
                assert category == Guardrails.TestPolicy.CATEGORY
                assert rationale == Guardrails.TestPolicy.RATIONALE
                return
        pytest.fail("Never got an unsafe result in 100 tries")
