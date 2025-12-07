"""Tests for classifier module (Llama Guard parsing)."""

import pytest

from neosian._foundation.guardrails.classifier import parse_classifier_response
from neosian._foundation.shared.exceptions import GuardrailClassifierParseError


@pytest.mark.unit
class TestParseClassifierResponse:
    """Test Llama Guard response parsing."""

    def test_safe_response(self) -> None:
        """Parse 'safe' response."""
        result = parse_classifier_response("safe")
        assert result.safe is True
        assert result.categories == []

    def test_safe_response_uppercase(self) -> None:
        """Parse 'SAFE' response (case insensitive)."""
        result = parse_classifier_response("SAFE")
        assert result.safe is True
        assert result.categories == []

    def test_safe_response_with_whitespace(self) -> None:
        """Parse 'safe' with leading/trailing whitespace."""
        result = parse_classifier_response("  safe  \n")
        assert result.safe is True
        assert result.categories == []

    def test_unsafe_single_category(self) -> None:
        """Parse unsafe response with single category."""
        result = parse_classifier_response("unsafe\nS1")
        assert result.safe is False
        assert result.categories == ["S1"]

    def test_unsafe_multiple_categories(self) -> None:
        """Parse unsafe response with multiple categories."""
        result = parse_classifier_response("unsafe\nS1,S3,S5")
        assert result.safe is False
        assert result.categories == ["S1", "S3", "S5"]

    def test_unsafe_categories_with_spaces(self) -> None:
        """Parse unsafe response with spaces in categories."""
        result = parse_classifier_response("unsafe\nS1, S3, S5")
        assert result.safe is False
        assert result.categories == ["S1", "S3", "S5"]

    def test_unsafe_no_categories(self) -> None:
        """Parse unsafe response without categories."""
        result = parse_classifier_response("unsafe\n")
        assert result.safe is False
        assert result.categories == []

    def test_unsafe_only(self) -> None:
        """Parse 'unsafe' without newline."""
        result = parse_classifier_response("unsafe")
        assert result.safe is False
        assert result.categories == []

    def test_unsafe_uppercase(self) -> None:
        """Parse 'UNSAFE' response (case insensitive)."""
        result = parse_classifier_response("UNSAFE\nS2")
        assert result.safe is False
        assert result.categories == ["S2"]

    def test_unsafe_categories_uppercase_normalized(self) -> None:
        """Categories should be uppercase in result."""
        result = parse_classifier_response("unsafe\ns1,s2")
        assert result.safe is False
        assert result.categories == ["S1", "S2"]

    def test_invalid_response_raises_error(self) -> None:
        """Invalid response should raise GuardrailClassifierParseError."""
        with pytest.raises(GuardrailClassifierParseError):
            parse_classifier_response("maybe")

    def test_empty_response_raises_error(self) -> None:
        """Empty response should raise GuardrailClassifierParseError."""
        with pytest.raises(GuardrailClassifierParseError):
            parse_classifier_response("")

    def test_whitespace_only_raises_error(self) -> None:
        """Whitespace-only response should raise error."""
        with pytest.raises(GuardrailClassifierParseError):
            parse_classifier_response("   \n\t  ")

    def test_s10_through_s14_categories(self) -> None:
        """Parse double-digit category codes."""
        result = parse_classifier_response("unsafe\nS10,S11,S12,S13,S14")
        assert result.safe is False
        assert result.categories == ["S10", "S11", "S12", "S13", "S14"]
