"""OpenAI wire-format converters — a pure move out of `openai.py`.

Extracted for size-gate headroom (the adapter sat one line from the 500
fail line); bodies are verbatim, `OpenAIClient`'s `_convert_*` methods
delegate here, and the field readers (`usage_of`, `refusal_of`,
`extra_of`) live beside them. Deliberately not shared with the other OpenAI-compatible
adapters: converters live per adapter until a real seam appears (the
anthropic allowlist reason, applied in reverse).
"""

from typing import Any, cast

from openai.types import CompletionUsage
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionMessageToolCallParam,
    ChatCompletionToolParam,
)
from openai.types.chat.completion_create_params import (
    ResponseFormat as OpenAIResponseFormat,
)
from openai.types.shared_params import FunctionDefinition

from neosian._foundation.llm.base import (
    Message,
    Role,
    ToolCall,
    ToolDefinition,
    Usage,
)
from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.exceptions import UnsupportedContentError
from neosian._foundation.shared.schema import strict_schema
from neosian._foundation.shared.serialization import safe_json_dumps
from neosian._foundation.shared.types import ResponseFormat


def usage_of(usage: CompletionUsage) -> Usage:
    details = getattr(usage, "prompt_tokens_details", None)
    cached_tokens = getattr(details, "cached_tokens", 0) or 0
    return Usage(
        input_tokens=usage.prompt_tokens - cached_tokens,
        output_tokens=usage.completion_tokens,
        cache_read_tokens=cached_tokens,
    )


def refusal_of(part: object) -> str | None:
    """OpenAI's `refusal` field off a message or delta (LL-14)."""
    value = getattr(part, "refusal", None)
    return value if isinstance(value, str) and value else None


def extra_of(part: object) -> dict[str, Any] | None:
    """The provider's own fields on a tool call or delta (the SDK keeps them
    in `model_extra`) — carried on `ToolCall.extra`, never interpreted."""
    extra = getattr(part, "model_extra", None)
    return dict(extra) if isinstance(extra, dict) and extra else None


def _tool_call_param(tc: ToolCall) -> ChatCompletionMessageToolCallParam:
    """The wire shape, plus the provider's own fields echoed back verbatim
    (Gemini 3's `extra_content.google.thought_signature`, DESIGN §19.7)."""
    param: dict[str, Any] = {
        "id": tc.id,
        "type": "function",
        "function": {
            "name": tc.name,
            "arguments": safe_json_dumps(tc.arguments, "tool_call.arguments"),
        },
    }
    if tc.extra:
        param.update(tc.extra)
    return cast(ChatCompletionMessageToolCallParam, param)


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
                    "tool_calls": [_tool_call_param(tc) for tc in msg.tool_calls],
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


def convert_tools(
    tools: list[ToolDefinition], *, strict_schemas: bool = True
) -> list[ChatCompletionToolParam]:
    """Convert internal tool definitions to OpenAI format.

    A strict tool is sent as one — `strict: true` with the schema in the
    strict-mode shape (`strict_schema`) — unless the door's
    `strict_schemas` is off, which drops the request (DESIGN §27.9).
    """
    result: list[ChatCompletionToolParam] = []
    for tool in tools:
        function: FunctionDefinition = {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        }
        if tool.strict and strict_schemas:
            function["parameters"] = strict_schema(tool.parameters)
            function["strict"] = True
        result.append({"type": "function", "function": function})
    return result


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
