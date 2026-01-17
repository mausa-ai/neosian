"""Unit tests for evaluation scorer."""

from neosian._foundation.evaluation.scorer import match_value, score_turn
from neosian._foundation.shared.constants import Evaluation
from neosian._foundation.shared.types import Expectation, ToolCallCapture


class TestMatchValue:
    """Tests for match_value function."""

    def test_exact_match_string(self) -> None:
        """Test exact string match."""
        matched, reason = match_value("hello", "hello")
        assert matched is True
        assert reason is None

    def test_exact_match_int(self) -> None:
        """Test exact integer match."""
        matched, reason = match_value(42, 42)
        assert matched is True
        assert reason is None

    def test_exact_match_float(self) -> None:
        """Test exact float match."""
        matched, reason = match_value(3.14, 3.14)
        assert matched is True
        assert reason is None

    def test_exists_marker_with_value(self) -> None:
        """Test _exists marker with present value."""
        matched, reason = match_value(Evaluation.EXISTS_MARKER, "anything")
        assert matched is True
        assert reason is None

    def test_exists_marker_with_none(self) -> None:
        """Test _exists marker with None value."""
        matched, reason = match_value(Evaluation.EXISTS_MARKER, None)
        assert matched is False
        assert reason == Evaluation.Scorer.PARAM_EXISTS_FAIL

    def test_type_coercion_int_to_float(self) -> None:
        """Test type coercion between int and float."""
        matched, reason = match_value(42, 42.0)
        assert matched is True
        assert reason is None

    def test_type_coercion_float_to_int(self) -> None:
        """Test type coercion from float to int."""
        matched, reason = match_value(42.0, 42)
        assert matched is True
        assert reason is None

    def test_string_coercion_int(self) -> None:
        """Test string representation match for numbers."""
        matched, reason = match_value("42", 42)
        assert matched is True
        assert reason is None

    def test_mismatch_different_strings(self) -> None:
        """Test mismatch with different strings."""
        matched, reason = match_value("hello", "world")
        assert matched is False
        assert reason is not None
        assert "hello" in reason
        assert "world" in reason

    def test_mismatch_different_numbers(self) -> None:
        """Test mismatch with different numbers."""
        matched, reason = match_value(42, 99)
        assert matched is False
        assert reason is not None


class TestScoreTurn:
    """Tests for score_turn function."""

    def test_no_tool_expected_none_called(self) -> None:
        """Test passing when no tool expected and none called."""
        expectation = Expectation(no_tool=True)
        result = score_turn(
            expectation=expectation,
            tool_calls=[],
            response_content="Just text response",
            turn_index=0,
        )
        assert result.passed is True
        assert result.expected_no_tool is True

    def test_no_tool_expected_but_tool_called(self) -> None:
        """Test failure when no tool expected but tool called."""
        expectation = Expectation(no_tool=True)
        tool_call = ToolCallCapture(name="some_tool", arguments={"a": 1})
        result = score_turn(
            expectation=expectation,
            tool_calls=[tool_call],
            response_content=None,
            turn_index=0,
        )
        assert result.passed is False
        assert result.expected_no_tool is True
        assert result.actual_tool == "some_tool"
        assert result.error is not None

    def test_tool_expected_and_called_correctly(self) -> None:
        """Test passing when expected tool called with correct params."""
        expectation = Expectation(
            tool="generate_image",
            params={"prompt": "a cat", "style": "realistic"},
        )
        tool_call = ToolCallCapture(
            name="generate_image",
            arguments={"prompt": "a cat", "style": "realistic", "extra": "ignored"},
        )
        result = score_turn(
            expectation=expectation,
            tool_calls=[tool_call],
            response_content=None,
            turn_index=0,
        )
        assert result.passed is True
        assert result.expected_tool == "generate_image"
        assert result.actual_tool == "generate_image"

    def test_tool_expected_but_wrong_tool_called(self) -> None:
        """Test failure when wrong tool called."""
        expectation = Expectation(tool="generate_image", params={})
        tool_call = ToolCallCapture(name="edit_image", arguments={})
        result = score_turn(
            expectation=expectation,
            tool_calls=[tool_call],
            response_content=None,
            turn_index=0,
        )
        assert result.passed is False
        assert result.expected_tool == "generate_image"
        assert result.actual_tool == "edit_image"
        assert result.error is not None

    def test_tool_expected_but_no_tool_called(self) -> None:
        """Test failure when expected tool not called."""
        expectation = Expectation(tool="generate_image", params={})
        result = score_turn(
            expectation=expectation,
            tool_calls=[],
            response_content="I cannot do that",
            turn_index=0,
        )
        assert result.passed is False
        assert result.expected_tool == "generate_image"
        assert result.actual_tool is None
        assert result.error is not None

    def test_tool_expected_with_wrong_params(self) -> None:
        """Test failure when tool called with wrong params."""
        expectation = Expectation(
            tool="generate_image",
            params={"prompt": "a cat"},
        )
        tool_call = ToolCallCapture(
            name="generate_image",
            arguments={"prompt": "a dog"},
        )
        result = score_turn(
            expectation=expectation,
            tool_calls=[tool_call],
            response_content=None,
            turn_index=0,
        )
        assert result.passed is False
        assert result.param_failures
        assert any("prompt" in f for f in result.param_failures)

    def test_tool_expected_with_exists_marker(self) -> None:
        """Test _exists marker in param expectations."""
        expectation = Expectation(
            tool="generate_image",
            params={"prompt": "_exists"},
        )
        tool_call = ToolCallCapture(
            name="generate_image",
            arguments={"prompt": "anything at all"},
        )
        result = score_turn(
            expectation=expectation,
            tool_calls=[tool_call],
            response_content=None,
            turn_index=0,
        )
        assert result.passed is True

    def test_sequence_expected_correct_order(self) -> None:
        """Test sequence expectation with correct tool order."""
        expectation = Expectation(
            sequence=[
                {"tool": "search", "params": {"query": "cats"}},
                {"tool": "summarize", "params": {}},
            ]
        )
        tool_calls = [
            ToolCallCapture(name="search", arguments={"query": "cats"}),
            ToolCallCapture(name="summarize", arguments={"text": "..."}),
        ]
        result = score_turn(
            expectation=expectation,
            tool_calls=tool_calls,
            response_content=None,
            turn_index=0,
        )
        assert result.passed is True

    def test_sequence_expected_wrong_order(self) -> None:
        """Test sequence expectation with wrong tool order."""
        expectation = Expectation(
            sequence=[
                {"tool": "search", "params": {}},
                {"tool": "summarize", "params": {}},
            ]
        )
        tool_calls = [
            ToolCallCapture(name="summarize", arguments={}),
            ToolCallCapture(name="search", arguments={}),
        ]
        result = score_turn(
            expectation=expectation,
            tool_calls=tool_calls,
            response_content=None,
            turn_index=0,
        )
        assert result.passed is False
        assert result.error is not None

    def test_no_expectation_always_passes(self) -> None:
        """Test that empty expectation always passes."""
        expectation = Expectation()
        result = score_turn(
            expectation=expectation,
            tool_calls=[],
            response_content="Hello",
            turn_index=5,
        )
        assert result.passed is True
        assert result.turn_index == 5

    def test_multiple_tools_finds_expected(self) -> None:
        """Test finding expected tool among multiple tool calls."""
        expectation = Expectation(
            tool="target_tool",
            params={"key": "value"},
        )
        tool_calls = [
            ToolCallCapture(name="other_tool", arguments={}),
            ToolCallCapture(name="target_tool", arguments={"key": "value"}),
            ToolCallCapture(name="another_tool", arguments={}),
        ]
        result = score_turn(
            expectation=expectation,
            tool_calls=tool_calls,
            response_content=None,
            turn_index=0,
        )
        assert result.passed is True
        assert result.actual_tool == "target_tool"
