"""Tool parameter constraints for JSON Schema generation.

These classes are used with typing.Annotated to add metadata
to tool parameters that gets converted to JSON Schema properties.

Example:
    @Tool(name="search", description="...")
    async def search(
        query: Annotated[str, Desc("Search query text")],
        limit: Annotated[int, Desc("Max results"), Min(1), Max(100)] = 10,
    ) -> ToolResult[list[str]]:
        ...
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Desc:
    """Parameter description for JSON Schema.

    Example:
        query: Annotated[str, Desc("Natural language search query")]
    """

    value: str


@dataclass(frozen=True, slots=True)
class Min:
    """Minimum value constraint for numbers.

    Maps to JSON Schema "minimum" property.

    Example:
        limit: Annotated[int, Min(1)]
    """

    value: int | float


@dataclass(frozen=True, slots=True)
class Max:
    """Maximum value constraint for numbers.

    Maps to JSON Schema "maximum" property.

    Example:
        limit: Annotated[int, Max(100)]
    """

    value: int | float


@dataclass(frozen=True, slots=True)
class MinLen:
    """Minimum length constraint for strings and arrays.

    Maps to JSON Schema "minLength" (strings) or "minItems" (arrays).

    Example:
        name: Annotated[str, MinLen(1)]
        tags: Annotated[list[str], MinLen(1)]
    """

    value: int


@dataclass(frozen=True, slots=True)
class MaxLen:
    """Maximum length constraint for strings and arrays.

    Maps to JSON Schema "maxLength" (strings) or "maxItems" (arrays).

    Example:
        name: Annotated[str, MaxLen(100)]
        tags: Annotated[list[str], MaxLen(10)]
    """

    value: int


@dataclass(frozen=True, slots=True)
class Pattern:
    """Regex pattern constraint for strings.

    Maps to JSON Schema "pattern" property.

    Example:
        email: Annotated[str, Pattern(r"^[\\w.-]+@[\\w.-]+\\.\\w+$")]
    """

    value: str


# Type alias for all constraint types
Constraint = Desc | Min | Max | MinLen | MaxLen | Pattern
