"""Cerebras wire-format converters — a pure move out of `cerebras.py`.

Extracted for size-gate headroom alongside `openai_convert.py` (the
adapter sat nine lines from the 500 fail line); bodies are verbatim,
`CerebrasClient`'s `_convert_*` methods delegate here. Deliberately not
shared with the other OpenAI-compatible adapters: converters live per
adapter until a real seam appears.
"""

from typing import Any

from neosian._foundation.llm.base import Message, Role, ToolDefinition
from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.exceptions import UnsupportedContentError
from neosian._foundation.shared.serialization import safe_json_dumps
from neosian._foundation.shared.types import ResponseFormat


def convert_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert internal messages to Cerebras format (OpenAI-compatible).

    Raises:
        UnsupportedContentError: On block-list content — neosian's
            Cerebras converter is text-only; media is never silently
            dropped.
    """
    result: list[dict[str, Any]] = []

    for msg in messages:
        if isinstance(msg.content, list):
            raise UnsupportedContentError(
                ErrorMessages.CONTENT_BLOCKS_NOT_SUPPORTED.format(
                    provider="cerebras", block_type="multimodal"
                )
            )
        if msg.role == Role.SYSTEM:
            result.append({"role": "system", "content": msg.content or ""})
        elif msg.role == Role.USER:
            result.append({"role": "user", "content": msg.content or ""})
        elif msg.role == Role.ASSISTANT:
            if msg.tool_calls:
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": safe_json_dumps(
                                    tc.arguments, "tool_call.arguments"
                                ),
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
                result.append(assistant_msg)
            else:
                result.append({"role": "assistant", "content": msg.content})
        elif msg.role == Role.TOOL:
            result.append(
                {
                    "role": "tool",
                    "content": msg.content or "",
                    "tool_call_id": msg.tool_call_id or "",
                }
            )

    return result


def convert_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    """Convert internal tool definitions to Cerebras format."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in tools
    ]


def convert_response_format(response_format: ResponseFormat) -> dict[str, object]:
    """Convert ResponseFormat to Cerebras json_schema format.

    Args:
        response_format: Internal ResponseFormat configuration.

    Returns:
        Cerebras-compatible response_format dict.
    """
    from neosian._foundation.shared.schema import get_json_schema, get_schema_name

    # Cerebras strict mode requires additionalProperties: false on every
    # object; get_json_schema guarantees it (root and $defs).
    schema = get_json_schema(response_format.schema)
    return {
        "type": "json_schema",
        "json_schema": {
            "name": get_schema_name(response_format.schema),
            "strict": response_format.strict,
            "schema": schema,
        },
    }
