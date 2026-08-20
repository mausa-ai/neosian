"""OpenAI wire-format converters — a pure move out of `openai.py`.

Extracted for size-gate headroom (the adapter sat one line from the 500
fail line); bodies are verbatim, `OpenAIClient`'s `_convert_*` methods
delegate here. Deliberately not shared with the other OpenAI-compatible
adapters: converters live per adapter until a real seam appears (the
anthropic allowlist reason, applied in reverse).
"""

from typing import cast

from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionToolParam,
)
from openai.types.chat.completion_create_params import (
    ResponseFormat as OpenAIResponseFormat,
)

from neosian._foundation.llm.base import Message, Role, ToolDefinition
from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.exceptions import UnsupportedContentError
from neosian._foundation.shared.serialization import safe_json_dumps
from neosian._foundation.shared.types import ResponseFormat


def convert_messages(messages: list[Message]) -> list[ChatCompletionMessageParam]:
    """Convert internal messages to OpenAI format.

    Raises:
        UnsupportedContentError: On block-list content — neosian's
            OpenAI converter is text-only; media is never silently
            dropped.
    """
    result: list[ChatCompletionMessageParam] = []

    for msg in messages:
        if isinstance(msg.content, list):
            raise UnsupportedContentError(
                ErrorMessages.CONTENT_BLOCKS_NOT_SUPPORTED.format(
                    provider="openai", block_type="multimodal"
                )
            )
        if msg.role == Role.SYSTEM:
            result.append({"role": "system", "content": msg.content or ""})
        elif msg.role == Role.USER:
            result.append({"role": "user", "content": msg.content or ""})
        elif msg.role == Role.ASSISTANT:
            if msg.tool_calls:
                assistant_msg: ChatCompletionAssistantMessageParam = {
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


def convert_tools(tools: list[ToolDefinition]) -> list[ChatCompletionToolParam]:
    """Convert internal tool definitions to OpenAI format."""
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


def convert_response_format(response_format: ResponseFormat) -> OpenAIResponseFormat:
    """Convert internal ResponseFormat to OpenAI response_format.

    Args:
        response_format: Internal ResponseFormat configuration.

    Returns:
        OpenAI-compatible response_format TypedDict.
    """
    from neosian._foundation.shared.schema import get_json_schema, get_schema_name

    # OpenAI strict mode requires additionalProperties: false on every
    # object; get_json_schema guarantees it (root and $defs).
    schema = get_json_schema(response_format.schema)
    # Cast to OpenAI ResponseFormat TypedDict - SDK accepts this structure
    return cast(
        OpenAIResponseFormat,
        {
            "type": "json_schema",
            "json_schema": {
                "name": get_schema_name(response_format.schema),
                "strict": response_format.strict,
                "schema": schema,
            },
        },
    )
