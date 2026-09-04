"""Tool system base primitives.

Provides the @Tool decorator and ToolResult for building agent tools.
"""

import inspect
import json
import types
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Literal,
    NotRequired,
    Required,
    Union,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
)

if TYPE_CHECKING:
    from neosian._foundation.memory.receipt import MemoryWriteReceipt

from neosian._foundation.llm.base import ToolDefinition
from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.constraints import (
    Constraint,
    Desc,
    Max,
    MaxLen,
    Min,
    MinLen,
    Pattern,
)
from neosian._foundation.shared.exceptions import ConfigurationError
from neosian._foundation.shared.serialization import safe_json_dumps
from neosian._foundation.shared.types import ToolFunction as ToolFunction, ToolName

# Dispatch is ``tool(**arguments)``: only keyword-bindable parameters are legal.
_BINDABLE_KINDS = (
    inspect.Parameter.POSITIONAL_OR_KEYWORD,
    inspect.Parameter.KEYWORD_ONLY,
)
_UNBINDABLE_PARAMETER = (
    "Tool {func} declares '{param}', which keyword dispatch can never bind"
)
_UNSERIALISABLE_DEFAULT = (
    "Tool {func} parameter '{name}' has a default that is not JSON: {default!r}"
)


@dataclass
class ToolResult[T]:
    """Result from a tool execution.

    Tools return either success with data or error with message.
    Optionally includes a system_reminder for agent guidance.

    Attributes:
        success: Whether the tool execution succeeded.
        data: The result data on success.
        error: Error message on failure.
        system_reminder: Optional guidance for the agent (hints, caveats, follow-ups).
        receipt: Structured record of a successful memory mutation (NP).
            In-process only — `to_json()` never carries it, so the wire
            envelope is byte-identical with or without one.
    """

    success: bool
    data: T | None = None
    error: str | None = None
    system_reminder: str | None = None
    receipt: "MemoryWriteReceipt | None" = None

    @classmethod
    def ok(
        cls,
        data: T,
        system_reminder: str | None = None,
        *,
        receipt: "MemoryWriteReceipt | None" = None,
    ) -> "ToolResult[T]":
        """Create a successful result.

        Args:
            data: The result data.
            system_reminder: Optional guidance for the agent.
            receipt: Structured memory-write record (in-process seam).
        """
        return cls(
            success=True, data=data, system_reminder=system_reminder, receipt=receipt
        )

    @classmethod
    def fail(cls, error: str, system_reminder: str | None = None) -> "ToolResult[T]":
        """Create a failed result.

        Args:
            error: Error message describing the failure.
            system_reminder: Optional guidance for the agent (e.g., retry hints).
        """
        return cls(success=False, error=error, system_reminder=system_reminder)

    def to_json(self) -> str:
        """Serialize to JSON string for LLM consumption.

        Deliberately excludes `receipt` — the wire envelope is frozen
        across transports (the CLI prints this verbatim, ledger #77) and
        the receipt is an in-process seam.

        Returns:
            JSON string with success/data/error and optional system_reminder.
        """
        if self.success:
            output: dict[str, Any] = {"success": True, "data": self.data}
        else:
            output = {"success": False, "error": self.error}

        if self.system_reminder:
            output["system_reminder"] = self.system_reminder

        return safe_json_dumps(output, "tool_result.data")


@dataclass
class ToolMetadata:
    """Metadata attached to a tool function; `origin` names where a
    library-bridged tool came from (for messages), None when decorated."""

    name: ToolName
    description: str
    definition: ToolDefinition
    origin: str | None = None


def _apply_constraints(schema: dict[str, Any], constraints: list[Constraint]) -> None:
    """Apply constraint metadata to a JSON Schema dict (mutates in place)."""
    schema_type = schema.get("type")

    for constraint in constraints:
        if isinstance(constraint, Desc):
            schema["description"] = constraint.value
        elif isinstance(constraint, Min):
            schema["minimum"] = constraint.value
        elif isinstance(constraint, Max):
            schema["maximum"] = constraint.value
        elif isinstance(constraint, MinLen):
            if schema_type == "string":
                schema["minLength"] = constraint.value
            elif schema_type == "array":
                schema["minItems"] = constraint.value
        elif isinstance(constraint, MaxLen):
            if schema_type == "string":
                schema["maxLength"] = constraint.value
            elif schema_type == "array":
                schema["maxItems"] = constraint.value
        elif isinstance(constraint, Pattern):
            schema["pattern"] = constraint.value


def _typeddict_to_json_schema(td_class: type[Any]) -> dict[str, Any]:
    """Convert a TypedDict class to JSON Schema.

    Handles Required[] and NotRequired[] annotations for determining
    which fields are required in the schema.
    """
    # Get type hints with extras to preserve Required/NotRequired
    hints = get_type_hints(td_class, include_extras=True)

    # Get required keys from TypedDict metadata
    # __required_keys__ and __optional_keys__ are set by TypedDict
    required_keys: frozenset[str] = getattr(td_class, "__required_keys__", frozenset())

    properties: dict[str, Any] = {}
    required: list[str] = []

    for field_name, field_type in hints.items():
        # Check if field is wrapped in Required[] or NotRequired[]
        origin = get_origin(field_type)
        actual_type = field_type

        if origin is Required:
            actual_type = get_args(field_type)[0]
            required.append(field_name)
        elif origin is NotRequired:
            actual_type = get_args(field_type)[0]
            # Not required, don't add to required list
        elif field_name in required_keys:
            required.append(field_name)

        # Convert the field type to schema
        properties[field_name] = _python_type_to_json_schema(actual_type)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _convert_basic_type(python_type: type[Any]) -> dict[str, Any]:
    """Convert a basic Python type to JSON Schema (no Annotated handling)."""
    # Handle None type
    if python_type is type(None):
        return {"type": "null"}

    # Basic types
    type_mapping: dict[type[Any], dict[str, str]] = {
        str: {"type": "string"},
        int: {"type": "integer"},
        float: {"type": "number"},
        bool: {"type": "boolean"},
    }

    if python_type in type_mapping:
        return dict(type_mapping[python_type])

    # Handle generic types (list, dict, Union, Literal, Enum)
    origin = get_origin(python_type)

    # Handle Literal types -> enum
    if origin is Literal:
        literal_values = get_args(python_type)
        if not literal_values:
            return {"type": "string"}

        # Infer type from first value
        first_val = literal_values[0]
        if isinstance(first_val, bool):
            json_type = "boolean"
        elif isinstance(first_val, int):
            json_type = "integer"
        elif isinstance(first_val, str):
            json_type = "string"
        else:
            json_type = "string"

        return {"type": json_type, "enum": list(literal_values)}

    # Handle list types
    if origin is list:
        args = get_args(python_type)
        item_type = args[0] if args else Any
        return {
            "type": "array",
            "items": _python_type_to_json_schema(item_type),
        }

    # Handle dict types
    if origin is dict:
        args = get_args(python_type)
        value_type = args[1] if args and len(args) > 1 else Any
        return {
            "type": "object",
            "additionalProperties": _python_type_to_json_schema(value_type),
        }

    # Handle Union types (including Optional[X] which is Union[X, None])
    if origin is Union or origin is types.UnionType:
        args = get_args(python_type)
        # Filter out NoneType to get the actual type(s)
        non_none_types = [t for t in args if t is not type(None)]
        if len(non_none_types) == 1:
            # Optional[X] case - return schema for X
            return _python_type_to_json_schema(non_none_types[0])
        elif len(non_none_types) > 1:
            # Union of multiple types - use anyOf
            return {"anyOf": [_python_type_to_json_schema(t) for t in non_none_types]}
        else:
            # Union[None] edge case
            return {"type": "null"}

    # Handle Enum classes
    if isinstance(python_type, type) and issubclass(python_type, Enum):
        values = [e.value for e in python_type]
        if not values:
            return {"type": "string"}

        first_val = values[0]
        if isinstance(first_val, bool):
            json_type = "boolean"
        elif isinstance(first_val, int):
            json_type = "integer"
        elif isinstance(first_val, str):
            json_type = "string"
        else:
            json_type = "string"

        return {"type": json_type, "enum": values}

    # Handle TypedDict classes
    if is_typeddict(python_type):
        return _typeddict_to_json_schema(python_type)

    # Default to string for unknown types
    return {"type": "string"}


def _python_type_to_json_schema(
    python_type: Any,  # a type or typing special form (Union, Literal, Annotated…)
    default: Any = inspect.Parameter.empty,
) -> dict[str, Any]:
    """Convert a Python type hint to JSON Schema type.

    Handles:
    - Basic types: str, int, float, bool
    - Collections: list[T], dict[K, V]
    - Unions: Optional[X], Union[X, Y], X | Y
    - Literal types: Literal["a", "b"] -> enum
    - Enum classes: MyEnum -> enum
    - Annotated types: Annotated[int, Desc("..."), Min(1), Max(100)]
    - Default values: included in schema if provided
    """
    constraints: list[Constraint] = []
    actual_type = python_type

    # Unwrap Annotated to get actual type + metadata
    origin = get_origin(python_type)
    if origin is Annotated:
        args = get_args(python_type)
        actual_type = args[0]
        # Extract constraint instances from Annotated metadata
        constraints = [a for a in args[1:] if isinstance(a, Constraint)]

    # Convert the actual type to schema
    schema = _convert_basic_type(actual_type)

    # Apply constraints from Annotated metadata
    if constraints:
        _apply_constraints(schema, constraints)

    # Include default value if provided
    if default is not inspect.Parameter.empty:
        schema["default"] = default

    return schema


def _extract_parameters_schema(func: Callable[..., Any]) -> dict[str, Any]:
    """Extract JSON Schema parameters from function signature.

    Generates a JSON Schema object with:
    - type: "object"
    - additionalProperties: false (prevents LLM hallucinating extra params)
    - properties: parameter schemas with types, descriptions, constraints, defaults
    - required: list of parameters without default values
    """
    sig = inspect.signature(func)
    # include_extras=True preserves Annotated metadata
    hints = get_type_hints(func, include_extras=True)

    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls", "return"):
            continue
        if param.kind not in _BINDABLE_KINDS:
            raise ConfigurationError(
                _UNBINDABLE_PARAMETER.format(func=func.__qualname__, param=param)
            )

        # Get type hint (default to str if not annotated)
        param_type = hints.get(param_name, str)

        default = param.default
        if default is not inspect.Parameter.empty:
            try:
                json.dumps(default)
            except (TypeError, ValueError) as exc:
                raise ConfigurationError(
                    _UNSERIALISABLE_DEFAULT.format(
                        func=func.__qualname__, name=param_name, default=default
                    )
                ) from exc

        # Build property schema with default value
        properties[param_name] = _python_type_to_json_schema(param_type, default)

        # Required only if no default value
        if default is inspect.Parameter.empty:
            required.append(param_name)

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


class Tool:
    """Decorator to create a tool from a function.

    Usage:
        @Tool(name="search", description="Search the web")
        async def search(query: str, limit: int = 10) -> ToolResult[list[str]]:
            results = await do_search(query, limit)
            return ToolResult.ok(results)
    """

    def __init__(self, name: str, description: str, *, strict: bool = False) -> None:
        """Initialize tool decorator.

        Args:
            name: Tool name for LLM tool calling.
            description: Description shown to the LLM.
            strict: Opt into provider-enforced constrained decoding (Anthropic
                only today; ignored by other providers). Strict guarantees the
                model emits arguments matching the schema but counts against
                Anthropic's per-request schema-complexity budget. Default
                False (best-effort).
        """
        self.name = ToolName(name)
        self.description = description
        self.strict = strict

    def __call__(self, func: ToolFunction) -> ToolFunction:
        """Apply decorator to function."""
        # Extract parameter schema from function signature
        parameters = _extract_parameters_schema(func)

        # Create tool definition
        definition = ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=parameters,
            strict=self.strict,
        )

        # Attach metadata to function
        metadata = ToolMetadata(
            name=self.name,
            description=self.description,
            definition=definition,
        )
        func._tool_metadata = metadata  # type: ignore[attr-defined]

        return func


def get_tool_metadata(func: Callable[..., Any]) -> ToolMetadata | None:
    """Get tool metadata from a decorated function."""
    return getattr(func, "_tool_metadata", None)


def get_tool_definition(func: Callable[..., Any]) -> ToolDefinition | None:
    """Get tool definition from a decorated function."""
    metadata = get_tool_metadata(func)
    return metadata.definition if metadata else None


def attach_tool_metadata(
    func: ToolFunction, definition: ToolDefinition, *, origin: str | None = None
) -> ToolFunction:
    """Attach a ready-made definition to a function the library built.

    Internal, reachable only through library factories — the eval
    harness's stub/override wrappers (DESIGN §13.6), the MCP bridge
    (§25) — the same library-only-mutator idiom as set_native_type
    below. Never reaches into an existing agent.
    """
    metadata = ToolMetadata(
        name=definition.name,
        description=definition.description,
        definition=definition,
        origin=origin,
    )
    func._tool_metadata = metadata  # type: ignore[attr-defined]
    return func


def set_native_type(func: ToolFunction, native_type: str) -> ToolFunction:
    """Mark a decorated tool's definition with a provider-native type.

    Internal, reachable only through library factories (ledger #41).
    Mutation is safe: every closure factory decorates a fresh function
    object, so the marked definition is never shared.

    Raises:
        ValueError: If function is not decorated with @Tool.
    """
    definition = get_tool_definition(func)
    if definition is None:
        raise ValueError(
            ErrorMessages.FUNCTION_NOT_DECORATED.format(func_name=func.__name__)
        )
    definition.native_type = native_type
    return func
