"""Schema utilities for ResponseFormat.

Provides unified handling for both BaseModel subclasses and Union types
using Pydantic's TypeAdapter.

Union types are automatically wrapped in an object schema because LLM APIs
(OpenAI, Anthropic, Cerebras) require root-level schemas to be objects, not anyOf.
The wrapper is: {"result": <union_value>}
"""

import json
import types
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel, TypeAdapter

# Type alias for schema types - supports both BaseModel classes and Union types
# Using Any to accommodate types.UnionType which mypy doesn't like mixing with type
SchemaType = type[BaseModel] | Any

# Key used to wrap union types in an object schema
_UNION_WRAPPER_KEY = "result"


def is_union_type(schema: SchemaType) -> bool:
    """Check if schema is a Union type.

    Args:
        schema: The type to check.

    Returns:
        True if schema is a Union type (Union[A, B] or A | B).
    """
    origin = get_origin(schema)
    # Handle typing.Union (e.g., Union[A, B])
    if origin is Union:
        return True
    # Handle types.UnionType (Python 3.10+ pipe syntax: A | B)
    # Use type() check to avoid mypy unreachable code error
    return type(schema) is types.UnionType


def get_schema_name(schema: SchemaType) -> str:
    """Get a descriptive name for a schema type.

    For BaseModel subclasses, returns the class name.
    For Union types, returns wrapped name format.

    Args:
        schema: BaseModel subclass or Union type.

    Returns:
        Schema name string (e.g., "WeatherResponse" or "TTSOutput_or_MusicOutput").
    """
    if is_union_type(schema):
        # get_args works for both typing.Union and types.UnionType
        args = get_args(schema)
        # For types.UnionType, use __args__ directly if get_args returns empty
        if not args and type(schema) is types.UnionType:
            args = schema.__args__
        names = [arg.__name__ for arg in args if hasattr(arg, "__name__")]
        return "_or_".join(names) if names else "UnionSchema"
    return schema.__name__


def _add_additional_properties_false(schema: Any) -> None:
    """Recursively add additionalProperties: false to all object schemas.

    LLM APIs (OpenAI, Anthropic, Cerebras) require additionalProperties: false on
    every object, including those in $defs.

    Args:
        schema: JSON schema value (dict or primitive) to process.
    """
    if not isinstance(schema, dict):
        return

    # If this is an object type, add additionalProperties: false
    if schema.get("type") == "object":
        schema["additionalProperties"] = False

    # Recurse into properties
    if "properties" in schema:
        for prop_schema in schema["properties"].values():
            _add_additional_properties_false(prop_schema)

    # Recurse into $defs
    if "$defs" in schema:
        for def_schema in schema["$defs"].values():
            _add_additional_properties_false(def_schema)

    # Recurse into anyOf/oneOf/allOf
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            for item_schema in schema[key]:
                _add_additional_properties_false(item_schema)

    # Recurse into items (for arrays)
    if "items" in schema:
        _add_additional_properties_false(schema["items"])


def get_json_schema(schema: SchemaType) -> dict[str, Any]:
    """Generate JSON schema for a type.

    Works for both BaseModel subclasses and Union types.
    Union types are wrapped in an object schema because LLM APIs require
    root-level schemas to be objects, not anyOf/oneOf.

    Invariant: every object schema in the result carries
    additionalProperties: false — including nested models under $defs. LLM
    APIs (Anthropic, OpenAI, Cerebras) reject object schemas without
    it, and Anthropic does so regardless of strict mode. Owning the
    invariant here keeps it provider-independent; adapters must not need to
    patch the schema themselves.

    Args:
        schema: BaseModel subclass or Union type.

    Returns:
        JSON schema dict. For unions, wrapped as {"result": <union>}.
    """
    if is_union_type(schema):
        # Get the union schema
        ta: TypeAdapter[Any] = TypeAdapter(schema)
        union_schema = ta.json_schema()

        # Wrap in object schema - LLM APIs require root to be object type
        # The wrapper uses a single "result" property containing the union
        wrapper_schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                _UNION_WRAPPER_KEY: union_schema,
            },
            "required": [_UNION_WRAPPER_KEY],
        }

        # Copy $defs if present (Pydantic puts discriminated union refs here)
        if "$defs" in union_schema:
            wrapper_schema["$defs"] = union_schema["$defs"]
            # Remove $defs from the nested schema to avoid duplication
            del wrapper_schema["properties"][_UNION_WRAPPER_KEY]["$defs"]

        # Add additionalProperties: false to all objects (required by LLM APIs)
        _add_additional_properties_false(wrapper_schema)

        return wrapper_schema

    # For BaseModel subclasses, use the native method. Pydantic emits nested
    # models under $defs without additionalProperties, so patch the whole
    # tree — root and every $defs entry.
    json_schema: dict[str, Any] = schema.model_json_schema()
    _add_additional_properties_false(json_schema)
    return json_schema


def validate_json(schema: SchemaType, json_string: str) -> Any:
    """Parse and validate JSON against a schema.

    Works for both BaseModel subclasses and Union types.
    For Union types, automatically unwraps from the {"result": ...} wrapper.

    Args:
        schema: BaseModel subclass or Union type.
        json_string: JSON string to parse and validate.

    Returns:
        Validated instance (BaseModel instance or Union member instance).
    """
    if is_union_type(schema):
        # Parse the wrapped JSON
        wrapped_data = json.loads(json_string)

        # Unwrap the result
        if isinstance(wrapped_data, dict) and _UNION_WRAPPER_KEY in wrapped_data:
            unwrapped_json = json.dumps(wrapped_data[_UNION_WRAPPER_KEY])
        else:
            # Fallback: maybe the LLM returned unwrapped (shouldn't happen)
            unwrapped_json = json_string

        # Validate and parse into the correct union member
        ta: TypeAdapter[Any] = TypeAdapter(schema)
        return ta.validate_json(unwrapped_json)

    # For BaseModel subclasses, use the native method
    return schema.model_validate_json(json_string)
