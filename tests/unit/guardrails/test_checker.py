"""Tests for policy checker module (GPT-OSS-Safeguard parsing)."""

import pytest

from neosian._foundation.guardrails.checker import parse_policy_response
from neosian._foundation.shared.exceptions import GuardrailPolicyParseError


@pytest.mark.unit
class TestParsePolicyResponse:
    """Test GPT-OSS-Safeguard response parsing."""

    def test_safe_response(self) -> None:
        """Parse safe response (violation=0).

        Note: category and rationale are always None for safe responses,
        regardless of what the model returns. Rationale only makes sense
        when explaining WHY something was blocked.
        """
        response = '{"violation": 0, "category": null, "rationale": "Content is safe"}'
        result = parse_policy_response(response)
        assert result.safe is True
        assert result.category is None
        assert result.rationale is None  # Rationale discarded for safe content

    def test_unsafe_response(self) -> None:
        """Parse unsafe response (violation=1)."""
        response = '{"violation": 1, "category": "P1", "rationale": "Prompt injection"}'
        result = parse_policy_response(response)
        assert result.safe is False
        assert result.category == "P1"
        assert result.rationale == "Prompt injection"

    def test_safe_with_missing_rationale(self) -> None:
        """Parse safe response without rationale."""
        response = '{"violation": 0, "category": null}'
        result = parse_policy_response(response)
        assert result.safe is True
        assert result.category is None
        assert result.rationale is None

    def test_safe_with_missing_category(self) -> None:
        """Parse safe response without category."""
        response = '{"violation": 0}'
        result = parse_policy_response(response)
        assert result.safe is True
        assert result.category is None

    def test_response_with_whitespace(self) -> None:
        """Parse response with leading/trailing whitespace."""
        response = '  {"violation": 0, "category": null}  \n'
        result = parse_policy_response(response)
        assert result.safe is True

    def test_violation_defaults_to_unsafe(self) -> None:
        """Missing violation field should default to unsafe (violation=1)."""
        response = '{"category": "P1", "rationale": "Something"}'
        result = parse_policy_response(response)
        assert result.safe is False

    def test_invalid_json_raises_error(self) -> None:
        """Invalid JSON should raise GuardrailPolicyParseError."""
        with pytest.raises(GuardrailPolicyParseError):
            parse_policy_response("not json")

    def test_empty_response_raises_error(self) -> None:
        """Empty response should raise GuardrailPolicyParseError."""
        with pytest.raises(GuardrailPolicyParseError):
            parse_policy_response("")

    def test_json_array_raises_error(self) -> None:
        """JSON array should raise GuardrailPolicyParseError."""
        with pytest.raises(GuardrailPolicyParseError):
            parse_policy_response('["violation", 0]')

    def test_violation_as_string_raises_error(self) -> None:
        """Violation as string should raise error (expects int)."""
        with pytest.raises(GuardrailPolicyParseError):
            parse_policy_response('{"violation": "0"}')

    def test_custom_category_code(self) -> None:
        """Parse response with custom category code."""
        response = '{"violation": 1, "category": "P10", "rationale": "Custom policy"}'
        result = parse_policy_response(response)
        assert result.safe is False
        assert result.category == "P10"
        assert result.rationale == "Custom policy"

    def test_long_rationale(self) -> None:
        """Parse response with long rationale."""
        rationale = "This is a very long rationale that explains in detail why " * 10
        response = f'{{"violation": 1, "category": "P1", "rationale": "{rationale}"}}'
        result = parse_policy_response(response)
        assert result.rationale == rationale
