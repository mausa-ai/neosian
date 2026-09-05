"""Tool system base primitives.

Provides the @Tool decorator for building agent tools; the schema and the
validator behind it live in `tools/schema.py`, `ToolResult` in
`tools/result.py` (re-exported here).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from neosian._foundation.llm.base import ToolDefinition
from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.types import ToolFunction as ToolFunction, ToolName
from neosian._foundation.tools.result import ToolResult as ToolResult
from neosian._foundation.tools.schema import arguments_schema, build_arguments_model


@dataclass
class ToolMetadata:
    """Metadata attached to a tool function; `origin` names where a
    library-bridged tool came from (for messages), None when decorated.
    `arguments` is the model the schema was generated from and every call
    is validated against — None for a definition the library attached
    (an MCP server's, an eval stub's), which binds instead."""

    name: ToolName
    description: str
    definition: ToolDefinition
    origin: str | None = None
    arguments: type[BaseModel] | None = None


class Tool:
    """Decorator to create a tool from a function.

    Usage:
        @Tool(name="search", description="Search the web")
        async def search(query: str, limit: int = 10) -> ToolResult[list[str]]:
            '''Search.

            Args:
                query: Free-text query.
                limit: Results to return.
            '''
            results = await do_search(query, limit)
            return ToolResult.ok(results)
    """

    def __init__(
        self,
        name: str,
        description: str,
        *,
        params: Mapping[str, str] | None = None,
        strict: bool = False,
    ) -> None:
        """Initialize tool decorator.

        Args:
            name: Tool name for LLM tool calling.
            description: Description shown to the LLM.
            params: Parameter descriptions by name, for prose that lives
                outside the function (the builtins' YAML). A `Desc()`
                annotation wins over it; a docstring `Args:` entry loses.
            strict: Opt into provider-enforced constrained decoding — on
                Anthropic, and on the OpenAI wire and Cerebras with the
                strict-mode schema shape (every property required, null
                defaults dropped). Counts against Anthropic's per-request
                schema-complexity budget. Default False (best-effort).
        """
        self.name = ToolName(name)
        self.description = description
        self.params = params
        self.strict = strict

    def __call__(self, func: ToolFunction) -> ToolFunction:
        """Apply decorator to function."""
        arguments = build_arguments_model(func, self.name, self.params)
        definition = ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=arguments_schema(arguments),
            strict=self.strict,
        )
        metadata = ToolMetadata(
            name=self.name,
            description=self.description,
            definition=definition,
            arguments=arguments,
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
    func: ToolFunction,
    definition: ToolDefinition,
    *,
    origin: str | None = None,
    arguments: type[BaseModel] | None = None,
) -> ToolFunction:
    """Attach a ready-made definition to a function the library built.

    Internal, reachable only through library factories — the eval
    harness's stub/override wrappers (DESIGN §13.6), the MCP bridge
    (§25) — the same library-only-mutator idiom as set_native_type
    below. Never reaches into an existing agent. `arguments` carries the
    validator along when the wrapper stands in for a decorated tool.
    """
    metadata = ToolMetadata(
        name=definition.name,
        description=definition.description,
        definition=definition,
        origin=origin,
        arguments=arguments,
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
