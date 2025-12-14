"""Tool system base primitives.

Provides the @Tool decorator and ToolResult for building agent tools.
"""

import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, get_type_hints

from neosian._foundation.llm.base import ToolDefinition
from neosian._foundation.shared.types import ToolFunction as ToolFunction
from neosian._foundation.shared.types import ToolName


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
    """

    success: bool
    data: T | None = None
    error: str | None = None
    system_reminder: str | None = None

    @classmethod
    def ok(cls, data: T, system_reminder: str | None = None) -> "ToolResult[T]":
        """Create a successful result.

        Args:
            data: The result data.
            system_reminder: Optional guidance for the agent.
        """
        return cls(success=True, data=data, system_reminder=system_reminder)

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

        Returns:
            JSON string with success/data/error and optional system_reminder.
        """
        if self.success:
            output: dict[str, Any] = {"success": True, "data": self.data}
        else:
            output = {"success": False, "error": self.error}

        if self.system_reminder:
            output["system_reminder"] = self.system_reminder

        return json.dumps(output)


@dataclass
class ToolMetadata:
    """Metadata attached to a tool function."""

    name: ToolName
    description: str
    definition: ToolDefinition


def _python_type_to_json_schema(python_type: type[Any]) -> dict[str, Any]:
    """Convert a Python type hint to JSON Schema type."""
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
        return type_mapping[python_type]

    # Handle generic types (list, dict, etc.)
    origin = getattr(python_type, "__origin__", None)

    if origin is list:
        args = getattr(python_type, "__args__", (Any,))
        item_type = args[0] if args else Any
        return {
            "type": "array",
            "items": _python_type_to_json_schema(item_type),
        }

    if origin is dict:
        args = getattr(python_type, "__args__", None)
        value_type = args[1] if args and len(args) > 1 else Any
        return {
            "type": "object",
            "additionalProperties": _python_type_to_json_schema(value_type),
        }

    # Default to string for unknown types
    return {"type": "string"}


def _extract_parameters_schema(func: Callable[..., Any]) -> dict[str, Any]:
    """Extract JSON Schema parameters from function signature."""
    sig = inspect.signature(func)
    hints = get_type_hints(func)

    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue

        # Get type hint
        param_type = hints.get(param_name, str)

        # Skip return type annotation
        if param_name == "return":
            continue

        # Build property schema
        properties[param_name] = _python_type_to_json_schema(param_type)

        # Check if required (no default value)
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    return {
        "type": "object",
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

    def __init__(self, name: str, description: str) -> None:
        """Initialize tool decorator.

        Args:
            name: Tool name for LLM tool calling.
            description: Description shown to the LLM.
        """
        self.name = ToolName(name)
        self.description = description

    def __call__(self, func: ToolFunction) -> ToolFunction:
        """Apply decorator to function."""
        # Extract parameter schema from function signature
        parameters = _extract_parameters_schema(func)

        # Create tool definition
        definition = ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=parameters,
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
