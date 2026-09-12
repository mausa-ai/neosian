"""OpenAI wire-format converters — a pure move out of `openai.py`.

Extracted for size-gate headroom (the adapter sat one line from the 500
fail line); bodies are verbatim, `OpenAIClient`'s `_convert_*` methods
delegate here, and the field readers (`usage_of`, `refusal_of`,
`extra_of`) live beside them. Deliberately not shared with the other OpenAI-compatible
adapters: converters live per adapter until a real seam appears (the
anthropic allowlist reason, applied in reverse).
"""

import json
from typing import Any, cast

from openai.types import CompletionUsage
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionMessageToolCallParam,
    ChatCompletionToolChoiceOptionParam,
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
from neosian._foundation.shared.prompt_assets import get_prompt
from neosian._foundation.shared.schema import strict_schema
from neosian._foundation.shared.serialization import safe_json_dumps
from neosian._foundation.shared.types import ResponseFormat, ToolChoice


def usage_of(usage: CompletionUsage) -> Usage:
    """Token counts off a usage block; a door may leave a count null (#218)."""
    details = getattr(usage, "prompt_tokens_details", None)
    cached_tokens = getattr(details, "cached_tokens", 0) or 0
    return Usage(
        input_tokens=(usage.prompt_tokens or 0) - cached_tokens,
        output_tokens=usage.completion_tokens or 0,
        cache_read_tokens=cached_tokens,
    )


def is_tool_call_error(body: object) -> bool:
    """Whether a 400's body names a tool-call generation failure.

    Two shapes on one wire (#218): OpenAI nests `{"error": {"code",
    "message"}}`; Cerebras answers the flat `{"code": "tool_use_failed",
    "message"}`.
    """
    if not isinstance(body, dict):
        return False
    err = body.get("error", body)
    if not isinstance(err, dict):
        return False
    code = str(err.get("code", ""))
    message = str(err.get("message", "")).lower()
    return (
        code in ("invalid_tool_call", "tool_use_failed")
        or "tool" in message
        or "function" in message
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


def convert_messages(
    messages: list[Message], *, echo_field: str | None = None
) -> list[ChatCompletionMessageParam]:
    """Convert internal messages to OpenAI format.

    `echo_field` names the door's reasoning field to send `Message.reasoning`
    back under on assistant turns (`OpenAICompatible.echo_reasoning`,
    DESIGN §31.4) — DeepSeek's documented 400 in a tool loop without it.

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
            assistant_msg: ChatCompletionAssistantMessageParam = {
                "role": "assistant",
                "content": msg.content,
            }
            if msg.tool_calls:
                assistant_msg["tool_calls"] = [
                    _tool_call_param(tc) for tc in msg.tool_calls
                ]
            if echo_field is not None and msg.reasoning:
                cast(dict[str, Any], assistant_msg)[echo_field] = msg.reasoning
            result.append(assistant_msg)
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


def convert_tool_choice(tool_choice: ToolChoice) -> ChatCompletionToolChoiceOptionParam:
    """Convert a ToolChoice to the OpenAI wire's `tool_choice` value.

    Three of the four modes are the wire's own strings; a named tool is
    its object form. `parallel` is not part of this value — it rides the
    body's own `parallel_tool_calls` flag.
    """
    if tool_choice.mode == "tool":
        return {
            "type": "function",
            "function": {"name": tool_choice.name or ""},
        }
    modes: dict[str, ChatCompletionToolChoiceOptionParam] = {
        "auto": "auto",
        "required": "required",
        "none": "none",
    }
    return modes[tool_choice.mode]


def tool_choice_body(
    tools: list[ChatCompletionToolParam] | None, tool_choice: ToolChoice | None
) -> dict[str, Any]:
    """The body keys a choice contributes — none at all without tools,
    since the wire takes a choice only beside a tool list, and
    `parallel_tool_calls` only when it says what the default does not.
    """
    if not tools or tool_choice is None:
        return {}
    body: dict[str, Any] = {"tool_choice": convert_tool_choice(tool_choice)}
    if not tool_choice.parallel:
        body["parallel_tool_calls"] = False
    return body


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


def json_object_format() -> OpenAIResponseFormat:
    """The plain JSON mode — a `json_object` door's response_format."""
    return {"type": "json_object"}


def schema_in_prompt(
    messages: list[Message], response_format: ResponseFormat
) -> list[Message]:
    """The schema a `json_object` door cannot take on the wire, prepended to
    the system prompt (one is inserted when the history has none); the
    instruction is the shipped `tools.json_object` asset. A new list — the
    caller's history is never rewritten.
    """
    from neosian._foundation.shared.schema import get_json_schema

    block = (
        f"{get_prompt('tools.json_object')}\n"
        f"{json.dumps(get_json_schema(response_format.schema))}"
    )
    if messages and messages[0].role == Role.SYSTEM:
        first = messages[0]
        content = first.content if isinstance(first.content, str) else ""
        system = Message(role=Role.SYSTEM, content=f"{block}\n\n{content}".rstrip())
        return [system, *messages[1:]]
    return [Message(role=Role.SYSTEM, content=block), *messages]
