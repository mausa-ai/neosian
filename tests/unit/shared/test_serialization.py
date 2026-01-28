"""Tests for JSON serialization utilities."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest

from neosian._foundation.shared.exceptions import MessageSerializationError
from neosian._foundation.shared.serialization import (
    _find_non_serializable,
    _is_json_serializable,
    safe_json_dumps,
)


@pytest.mark.unit
class TestIsJsonSerializable:
    """Tests for _is_json_serializable helper."""

    def test_primitives_are_serializable(self) -> None:
        """Primitive types should be serializable."""
        assert _is_json_serializable(None) is True
        assert _is_json_serializable("string") is True
        assert _is_json_serializable(42) is True
        assert _is_json_serializable(3.14) is True
        assert _is_json_serializable(True) is True
        assert _is_json_serializable(False) is True

    def test_dict_with_primitives_is_serializable(self) -> None:
        """Dict with primitive values should be serializable."""
        assert _is_json_serializable({"a": 1, "b": "two"}) is True
        assert _is_json_serializable({"nested": {"value": 42}}) is True

    def test_list_with_primitives_is_serializable(self) -> None:
        """List with primitive values should be serializable."""
        assert _is_json_serializable([1, 2, 3]) is True
        assert _is_json_serializable(["a", "b", "c"]) is True
        assert _is_json_serializable([{"a": 1}, {"b": 2}]) is True

    def test_decimal_is_not_serializable(self) -> None:
        """Decimal should not be serializable."""
        assert _is_json_serializable(Decimal("10.5")) is False

    def test_datetime_is_not_serializable(self) -> None:
        """datetime should not be serializable."""
        assert _is_json_serializable(datetime.now()) is False

    def test_uuid_is_not_serializable(self) -> None:
        """UUID should not be serializable."""
        assert (
            _is_json_serializable(UUID("12345678-1234-5678-1234-567812345678")) is False
        )

    def test_dict_with_decimal_is_not_serializable(self) -> None:
        """Dict containing Decimal should not be serializable."""
        assert _is_json_serializable({"amount": Decimal("19.99")}) is False

    def test_list_with_decimal_is_not_serializable(self) -> None:
        """List containing Decimal should not be serializable."""
        assert _is_json_serializable([Decimal("1"), Decimal("2")]) is False

    def test_nested_decimal_is_not_serializable(self) -> None:
        """Deeply nested Decimal should not be serializable."""
        data = {"order": {"items": [{"price": Decimal("9.99")}]}}
        assert _is_json_serializable(data) is False


@pytest.mark.unit
class TestFindNonSerializable:
    """Tests for _find_non_serializable helper."""

    def test_returns_empty_for_primitives(self) -> None:
        """Should return empty for serializable primitives."""
        field, type_name = _find_non_serializable("string")
        assert field is None
        assert type_name == ""

    def test_finds_decimal_at_root(self) -> None:
        """Should find Decimal at root level."""
        field, type_name = _find_non_serializable(Decimal("10"))
        assert field is None
        assert type_name == "Decimal"

    def test_finds_decimal_in_dict(self) -> None:
        """Should find Decimal in dict and return field path."""
        field, type_name = _find_non_serializable({"amount": Decimal("19.99")})
        assert field == "amount"
        assert type_name == "Decimal"

    def test_finds_decimal_in_nested_dict(self) -> None:
        """Should find Decimal in nested dict with full path."""
        data = {"order": {"total": Decimal("99.99")}}
        field, type_name = _find_non_serializable(data)
        assert field == "order.total"
        assert type_name == "Decimal"

    def test_finds_decimal_in_list(self) -> None:
        """Should find Decimal in list with index."""
        data = [Decimal("1"), Decimal("2")]
        field, type_name = _find_non_serializable(data)
        assert field == "[0]"
        assert type_name == "Decimal"

    def test_finds_decimal_in_nested_list(self) -> None:
        """Should find Decimal in nested structure."""
        data = {"items": [{"price": Decimal("9.99")}]}
        field, type_name = _find_non_serializable(data)
        assert field == "items[0].price"
        assert type_name == "Decimal"

    def test_finds_datetime(self) -> None:
        """Should find datetime and return type name."""
        data = {"created_at": datetime.now()}
        field, type_name = _find_non_serializable(data)
        assert field == "created_at"
        assert type_name == "datetime"

    def test_finds_uuid(self) -> None:
        """Should find UUID and return type name."""
        data = {"id": UUID("12345678-1234-5678-1234-567812345678")}
        field, type_name = _find_non_serializable(data)
        assert field == "id"
        assert type_name == "UUID"


@pytest.mark.unit
class TestSafeJsonDumps:
    """Tests for safe_json_dumps function."""

    def test_serializes_valid_json(self) -> None:
        """Should serialize valid JSON data."""
        data = {"name": "test", "count": 42, "active": True}
        result = safe_json_dumps(data, "test.data")
        assert result == '{"name": "test", "count": 42, "active": true}'

    def test_serializes_nested_data(self) -> None:
        """Should serialize nested structures."""
        data = {"items": [{"id": 1}, {"id": 2}]}
        result = safe_json_dumps(data, "test.data")
        assert '"items"' in result
        assert '"id"' in result

    def test_raises_for_decimal(self) -> None:
        """Should raise MessageSerializationError for Decimal."""
        data = {"amount": Decimal("19.99")}
        with pytest.raises(MessageSerializationError) as exc_info:
            safe_json_dumps(data, "tool_call.arguments")

        error = exc_info.value
        assert error.context == "tool_call.arguments"
        assert error.field == "amount"
        assert error.value_type == "Decimal"

    def test_raises_for_nested_decimal(self) -> None:
        """Should raise with full field path for nested Decimal."""
        data = {"order": {"items": [{"price": Decimal("9.99")}]}}
        with pytest.raises(MessageSerializationError) as exc_info:
            safe_json_dumps(data, "tool_result.data")

        error = exc_info.value
        assert error.context == "tool_result.data"
        assert error.field == "order.items[0].price"
        assert error.value_type == "Decimal"

    def test_raises_for_datetime(self) -> None:
        """Should raise MessageSerializationError for datetime."""
        data = {"timestamp": datetime(2024, 1, 1, 12, 0, 0)}
        with pytest.raises(MessageSerializationError) as exc_info:
            safe_json_dumps(data, "sse_event.data")

        error = exc_info.value
        assert error.field == "timestamp"
        assert error.value_type == "datetime"

    def test_error_message_includes_field_path(self) -> None:
        """Error message should include the field path."""
        data = {"order": {"total": Decimal("99.99")}}
        with pytest.raises(MessageSerializationError) as exc_info:
            safe_json_dumps(data, "tool_call.arguments")

        message = str(exc_info.value)
        assert "order.total" in message
        assert "Decimal" in message
        assert "tool_call.arguments" in message

    def test_error_message_without_field_path(self) -> None:
        """Error message should work without field path."""
        with pytest.raises(MessageSerializationError) as exc_info:
            safe_json_dumps(Decimal("10"), "test.data")

        message = str(exc_info.value)
        assert "Decimal" in message
        assert "test.data" in message

    def test_preserves_original_error(self) -> None:
        """Should preserve original TypeError in exception chain."""
        data = {"value": Decimal("10")}
        with pytest.raises(MessageSerializationError) as exc_info:
            safe_json_dumps(data, "test")

        assert exc_info.value.__cause__ is not None
        assert isinstance(exc_info.value.__cause__, TypeError)
