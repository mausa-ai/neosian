"""JSON serialization utilities with improved error messages.

Provides safe serialization that identifies problematic fields
when non-JSON-serializable types are encountered.
"""

import json
from typing import Any

from neosian._foundation.shared.exceptions import MessageSerializationError


def _is_json_serializable(value: Any) -> bool:
    """Check if a value is JSON-serializable.

    Args:
        value: The value to check.

    Returns:
        True if the value can be serialized to JSON.
    """
    if value is None:
        return True
    if isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, dict):
        return all(
            isinstance(k, str) and _is_json_serializable(v) for k, v in value.items()
        )
    if isinstance(value, list):
        return all(_is_json_serializable(item) for item in value)
    return False


def _find_non_serializable(data: Any, path: str = "") -> tuple[str | None, str]:
    """Recursively find the first non-JSON-serializable field.

    Args:
        data: The data structure to inspect.
        path: Current path in the data structure (for recursion).

    Returns:
        Tuple of (field_path, type_name) for the first non-serializable value.
        Returns (None, type_name) if the root value itself is non-serializable.
    """
    # JSON-serializable primitives
    if data is None or isinstance(data, (str, int, float, bool)):
        return None, ""

    # Check dict recursively
    if isinstance(data, dict):
        for key, value in data.items():
            current_path = f"{path}.{key}" if path else key
            # Check if value itself is non-serializable
            if not _is_json_serializable(value):
                # If it's a nested structure, recurse to find the exact field
                if isinstance(value, (dict, list)):
                    result = _find_non_serializable(value, current_path)
                    if result[0] is not None:
                        return result
                # It's a non-serializable leaf value
                return current_path, type(value).__name__
        return None, ""

    # Check list recursively
    if isinstance(data, list):
        for i, item in enumerate(data):
            current_path = f"{path}[{i}]"
            if not _is_json_serializable(item):
                # If it's a nested structure, recurse
                if isinstance(item, (dict, list)):
                    result = _find_non_serializable(item, current_path)
                    if result[0] is not None:
                        return result
                # It's a non-serializable leaf value
                return current_path, type(item).__name__
        return None, ""

    # The data itself is non-serializable (not a primitive, dict, or list)
    return path or None, type(data).__name__


def safe_json_dumps(data: Any, context: str, *, compact: bool = False) -> str:
    """Serialize data to JSON with helpful error messages.

    Attempts JSON serialization and on failure, identifies the specific
    field and type that caused the error.

    Args:
        data: The data to serialize.
        context: Description of what's being serialized (e.g., "tool_call.arguments").
        compact: Emit the tightest separators (the SSE wire form).

    Returns:
        JSON string representation of the data.

    Raises:
        MessageSerializationError: If data contains non-JSON-serializable types.
            The error message includes the specific field path and type.
    """
    try:
        return json.dumps(data, separators=(",", ":") if compact else None)
    except TypeError as e:
        # Find the problematic field
        field, value_type = _find_non_serializable(data)

        if not value_type:
            value_type = "unknown"

        raise MessageSerializationError(
            context=context,
            field=field,
            value_type=value_type,
            error=str(e),
        ) from e
