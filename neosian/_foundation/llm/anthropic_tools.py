"""Anthropic's tool, tool-choice and output-schema wire formats.

Split out of `anthropic_convert.py` at NC9 for the reason that file was
split out of `anthropic.py` at NC7 (REVIEW LL-31): the schema half has
its own seam. Messages are a function of the conversation; these are a
function of the tools and the schema, and nothing here reads a message.
"""

import copy
from typing import Any

from neosian._foundation.llm.base import ToolDefinition
from neosian._foundation.shared.types import CacheTtl, ResponseFormat, ToolChoice

# Anthropic rejects these on integer/number fields regardless of strict mode.
_ALWAYS_UNSUPPORTED_NUMERIC_KEYS = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
    }
)
# Strict-mode-only rejections (per Anthropic structured-outputs docs).
_STRICT_UNSUPPORTED_NUMERIC_KEYS = frozenset({"multipleOf"})
_STRICT_UNSUPPORTED_STRING_KEYS = frozenset({"minLength", "maxLength"})
# minItems/maxItems are allowed only when 0 or 1 under strict mode.
_STRICT_BOUNDED_ARRAY_KEYS = frozenset({"minItems", "maxItems"})


def _strip_unsupported_recursive(schema: Any, *, strict: bool) -> None:
    """Recursively strip Anthropic-incompatible keywords in place."""
    if not isinstance(schema, dict):
        return

    schema_type = schema.get("type")
    if schema_type in ("integer", "number"):
        for key in _ALWAYS_UNSUPPORTED_NUMERIC_KEYS:
            schema.pop(key, None)
        if strict:
            for key in _STRICT_UNSUPPORTED_NUMERIC_KEYS:
                schema.pop(key, None)
    elif schema_type == "string" and strict:
        for key in _STRICT_UNSUPPORTED_STRING_KEYS:
            schema.pop(key, None)
    elif schema_type == "array" and strict:
        for key in _STRICT_BOUNDED_ARRAY_KEYS:
            value = schema.get(key)
            if isinstance(value, int) and value > 1:
                schema.pop(key, None)

    if "properties" in schema:
        for prop_schema in schema["properties"].values():
            _strip_unsupported_recursive(prop_schema, strict=strict)

    if "$defs" in schema:
        for def_schema in schema["$defs"].values():
            _strip_unsupported_recursive(def_schema, strict=strict)

    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            for item in schema[key]:
                _strip_unsupported_recursive(item, strict=strict)

    if "items" in schema:
        _strip_unsupported_recursive(schema["items"], strict=strict)


def strip_unsupported_constraints(
    schema: dict[str, Any], *, strict: bool
) -> dict[str, Any]:
    """Remove JSON Schema keywords Anthropic rejects.

    Always strips: minimum/maximum/exclusiveMinimum/exclusiveMaximum on
    integer/number (Anthropic rejects these regardless of strict mode).

    When strict=True, additionally strips: multipleOf on numbers; minLength
    and maxLength on strings; minItems/maxItems > 1 on arrays. Per the
    Anthropic structured-outputs docs, these are rejected only in strict mode.

    `pattern` is preserved; only specific regex constructs (backreferences,
    lookaheads, word boundaries) are rejected, and detecting those is left as
    a follow-up if real bugs appear.

    Returns a sanitized deep copy; the input is not mutated.
    """
    result: dict[str, Any] = copy.deepcopy(schema)
    _strip_unsupported_recursive(result, strict=strict)
    return result


def cache_control(ttl: CacheTtl = "5m") -> dict[str, Any]:
    """The cache_control marker for a breakpoint at `ttl`.

    "5m" is the wire's own default, so it is sent as the bare marker and
    no request changes shape for an agent that never asked for anything
    else. The `ttl` key appears only for "1h" (NC9, #227).
    """
    if ttl == "1h":
        return {"type": "ephemeral", "ttl": "1h"}
    return {"type": "ephemeral"}


def convert_tool_choice(tool_choice: ToolChoice) -> dict[str, Any]:
    """Convert a ToolChoice to Anthropic's `tool_choice` value.

    The four modes are Anthropic's four types; `parallel=False` becomes
    `disable_parallel_tool_use`, which "none" has no room for (nothing is
    called, so nothing can be parallel).
    """
    types: dict[str, str] = {
        "auto": "auto",
        "required": "any",
        "none": "none",
        "tool": "tool",
    }
    value: dict[str, Any] = {"type": types[tool_choice.mode]}
    if tool_choice.name is not None:
        value["name"] = tool_choice.name
    if not tool_choice.parallel and tool_choice.mode != "none":
        value["disable_parallel_tool_use"] = True
    return value


def convert_tools(
    tools: list[ToolDefinition], ttl: CacheTtl = "5m"
) -> list[dict[str, Any]]:
    """Convert internal tool definitions to Anthropic format.

    Tools with strict=True opt into provider-enforced constrained decoding.
    Anthropic enforces a complexity budget across the aggregated strict
    tool schemas (cap of 20 strict tools per request, plus a per-request
    schema-complexity ceiling). Non-strict tools pass through as
    best-effort hints and do not count against either limit.

    Adds cache_control to the last tool definition. Anthropic caches
    everything up to and including the marked block, so marking the
    last tool caches all tool definitions.

    Tools carrying `native_type` become the schema-less native
    declaration ({"type", "name"} only) — no description or
    input_schema; the model's trained behavior replaces both.
    cache_control remains valid on native entries.

    Args:
        tools: Internal ToolDefinition objects.

    Returns:
        Anthropic-formatted tool definitions with cache_control on last.
    """
    result: list[dict[str, Any]] = []
    for tool in tools:
        if tool.native_type is not None:
            result.append({"type": tool.native_type, "name": tool.name})
            continue
        input_schema = strip_unsupported_constraints(
            tool.parameters, strict=tool.strict
        )
        entry: dict[str, Any] = {
            "name": tool.name,
            "description": tool.description,
            "input_schema": input_schema,
        }
        if tool.strict:
            entry["strict"] = True
            if (
                input_schema.get("type") == "object"
                and "additionalProperties" not in input_schema
            ):
                input_schema["additionalProperties"] = False
        result.append(entry)
    if result:
        result[-1]["cache_control"] = cache_control(ttl)
    return result


def convert_response_format(response_format: ResponseFormat) -> dict[str, object]:
    """Convert ResponseFormat to the value of Anthropic's output_config.format.

    Args:
        response_format: Internal ResponseFormat configuration.

    Returns:
        Anthropic-compatible format spec (placed at output_config["format"]).
    """
    from neosian._foundation.shared.schema import get_json_schema

    # get_json_schema guarantees additionalProperties: false on every
    # object (root and $defs). Anthropic requires it on output-format
    # schemas regardless of strict mode, so no adapter-side patch here.
    schema = strip_unsupported_constraints(
        get_json_schema(response_format.schema), strict=response_format.strict
    )
    return {
        "type": "json_schema",
        "schema": schema,
    }
