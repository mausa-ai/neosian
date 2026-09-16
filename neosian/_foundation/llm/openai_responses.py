"""The Responses wire's body codec (DESIGN §31.5, ledger #250–#252, #257).

Beside `openai_convert.py`, which is Chat Completions'. One client speaks
both wires (`OpenAICompatible.wire`); this module is what a `responses`
door sends and reads: the input items replayed from a history, the flat
function tools, the text format, the request with `store: false` and
encrypted reasoning asked for, and the parse of one `Response`. Reasoning
items ride `Message.extra["openai"]` — the opaque provider channel (#172,
#255) — and are replayed verbatim ahead of the assistant turn that
produced them, so the model reasons across a tool loop with nothing
stored at the provider.
"""

from __future__ import annotations

from typing import Any, Final, cast

from openai import Omit, omit
from openai.types.responses import (
    FunctionToolParam,
    Response,
    ResponseFunctionToolCall,
    ResponseInputItemParam,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseReasoningItem,
    ResponseTextConfigParam,
    ResponseUsage,
)
from openai.types.shared_params import Reasoning

from neosian._foundation.llm.base import (
    CompletionResponse,
    Message,
    Role,
    ToolCall,
    ToolDefinition,
    Usage,
)
from neosian._foundation.llm.errors import tool_arguments
from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.exceptions import UnsupportedContentError
from neosian._foundation.shared.schema import (
    get_json_schema,
    get_schema_name,
    strict_schema,
)
from neosian._foundation.shared.serialization import safe_json_dumps
from neosian._foundation.shared.types import (
    AnyModel,
    OpenAICompatible,
    ReasoningEffort,
    ResponseFormat,
    ToolCallId,
    ToolChoice,
    ToolName,
)

CHANNEL: Final = "openai"
ITEMS: Final = "reasoning_items"
# Encrypted reasoning is the only state the loop needs back (#252).
INCLUDE: Final = ("reasoning.encrypted_content",)


def reasoning_items_of(message: Message) -> list[dict[str, Any]]:
    """The Responses reasoning items a message carries on its channel."""
    channel = (message.extra or {}).get(CHANNEL)
    items = channel.get(ITEMS) if isinstance(channel, dict) else None
    return [dict(item) for item in items] if isinstance(items, list) else []


def reasoning_channel(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The `Message.extra` a turn carries: the channel only when there is
    something to replay, so every other turn encodes exactly as before."""
    return {CHANNEL: {ITEMS: items}} if items else None


def reasoning_item(item: ResponseReasoningItem) -> dict[str, Any] | None:
    """The item as the wire takes it back. One without encrypted content
    cannot be replayed statelessly and is dropped."""
    if not item.encrypted_content:
        return None
    return {
        "type": "reasoning",
        "id": item.id,
        "summary": [{"type": "summary_text", "text": s.text} for s in item.summary],
        "encrypted_content": item.encrypted_content,
    }


def convert_input(messages: list[Message]) -> list[ResponseInputItemParam]:
    """A history as Responses input items: an assistant turn replays its
    reasoning items, then its text, then one `function_call` per call;
    a tool turn is the call's output.

    Raises:
        UnsupportedContentError: On block-list content — the wire's
            converter is text-only; media is never silently dropped.
    """
    items: list[ResponseInputItemParam] = []
    for msg in messages:
        if isinstance(msg.content, list):
            raise UnsupportedContentError(
                ErrorMessages.CONTENT_BLOCKS_NOT_SUPPORTED.format(
                    provider="openai", block_type="multimodal"
                )
            )
        if msg.role == Role.SYSTEM:
            items.append({"role": "system", "content": msg.content or ""})
        elif msg.role == Role.USER:
            items.append({"role": "user", "content": msg.content or ""})
        elif msg.role == Role.ASSISTANT:
            items.extend(
                cast(ResponseInputItemParam, item) for item in reasoning_items_of(msg)
            )
            if msg.content:
                items.append({"role": "assistant", "content": msg.content})
            for tc in msg.tool_calls:
                items.append(
                    {
                        "type": "function_call",
                        "call_id": tc.id,
                        "name": tc.name,
                        "arguments": safe_json_dumps(
                            tc.arguments, "tool_call.arguments"
                        ),
                    }
                )
        elif msg.role == Role.TOOL:
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": msg.tool_call_id or "",
                    "output": msg.content or "",
                }
            )
    return items


def convert_tools(
    tools: list[ToolDefinition], *, strict_schemas: bool = True
) -> list[FunctionToolParam]:
    """The flat function-tool shape; `strict` as on Chat Completions (§27.9)."""
    result: list[FunctionToolParam] = []
    for tool in tools:
        strict = tool.strict and strict_schemas
        result.append(
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": (
                    strict_schema(tool.parameters) if strict else tool.parameters
                ),
                "strict": strict,
            }
        )
    return result


def text_format(
    response_format: ResponseFormat, *, strict_schemas: bool = True
) -> ResponseTextConfigParam:
    """The schema on the wire — a `responses` door has no other mode (#257)."""
    return {
        "format": {
            "type": "json_schema",
            "name": get_schema_name(response_format.schema),
            "schema": get_json_schema(response_format.schema),
            "strict": response_format.strict and strict_schemas,
        }
    }


def tool_choice_body(
    tools: list[FunctionToolParam] | None, tool_choice: ToolChoice | None
) -> dict[str, Any]:
    """The same four modes as Chat Completions', in Responses' spelling."""
    if not tools or tool_choice is None:
        return {}
    choice: Any = (
        {"type": "function", "name": tool_choice.name or ""}
        if tool_choice.mode == "tool"
        else tool_choice.mode
    )
    body: dict[str, Any] = {"tool_choice": choice}
    if not tool_choice.parallel:
        body["parallel_tool_calls"] = False
    return body


def reasoning_body(effort: ReasoningEffort | None) -> Reasoning | Omit:
    """The effort asked for, with a summary so reasoning reaches
    `Message.reasoning`; nothing when none was asked, so a row without
    a `none` rung (Astra) keeps the provider's default."""
    if effort is None:
        return omit
    return {"effort": cast(Any, effort.value), "summary": "auto"}


def request_kwargs(
    *,
    model: AnyModel,
    messages: list[Message],
    tools: list[ToolDefinition] | None,
    tool_choice: ToolChoice | None,
    temperature: float | None,
    response_format: ResponseFormat | None,
    effort: ReasoningEffort | None,
    max_tokens: int,
    door: OpenAICompatible,
) -> dict[str, Any]:
    """One request body: stateless, encrypted reasoning asked for."""
    function_tools = (
        convert_tools(tools, strict_schemas=door.strict_schemas) if tools else None
    )
    return {
        "model": model.value,
        "input": convert_input(messages),
        "tools": function_tools if function_tools else omit,
        **tool_choice_body(function_tools, tool_choice),
        "max_output_tokens": max_tokens,
        "temperature": temperature if temperature is not None else omit,
        "text": (
            text_format(response_format, strict_schemas=door.strict_schemas)
            if response_format
            else omit
        ),
        "reasoning": reasoning_body(effort),
        "store": False,
        "include": list(INCLUDE),
    }


def usage_of(usage: ResponseUsage) -> Usage:
    """Token counts off a usage block; `input_tokens` includes the cached
    ones, so the non-cached count is the difference (ECOSYSTEM §3)."""
    details = getattr(usage, "input_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    return Usage(
        input_tokens=(usage.input_tokens or 0) - cached,
        output_tokens=usage.output_tokens or 0,
        cache_read_tokens=cached,
    )


def finish_reason(response: Response, *, refused: bool, calls: bool) -> str:
    """Chat Completions' vocabulary, so every consumer reads it unchanged:
    a cap is `length`, a refusal `refusal`, a call `tool_calls`."""
    details = response.incomplete_details
    if details is not None and details.reason is not None:
        return "length" if details.reason == "max_output_tokens" else details.reason
    if refused:
        return "refusal"
    return "tool_calls" if calls else "stop"


def parse_response(response: Response, door: OpenAICompatible) -> CompletionResponse:
    """One `Response` as a `CompletionResponse`: text, refusal, calls,
    the reasoning summary on `reasoning`, the items on the channel."""
    texts: list[str] = []
    refusals: list[str] = []
    calls: list[ResponseFunctionToolCall] = []
    items: list[dict[str, Any]] = []
    summaries: list[str] = []
    for item in response.output:
        if isinstance(item, ResponseOutputMessage):
            for part in item.content:
                if isinstance(part, ResponseOutputText):
                    texts.append(part.text)
                else:
                    refusals.append(part.refusal)
        elif isinstance(item, ResponseFunctionToolCall):
            calls.append(item)
        elif isinstance(item, ResponseReasoningItem):
            summaries.extend(s.text for s in item.summary if s.text)
            if (kept := reasoning_item(item)) is not None:
                items.append(kept)
    stop_reason = finish_reason(response, refused=bool(refusals), calls=bool(calls))
    return CompletionResponse(
        message=Message(
            role=Role.ASSISTANT,
            content="".join(texts) or "".join(refusals) or None,
            tool_calls=[
                ToolCall(
                    id=ToolCallId(call.call_id),
                    name=ToolName(call.name),
                    arguments=tool_arguments(
                        door.name, call.arguments, stop_reason=stop_reason
                    ),
                )
                for call in calls
            ],
            reasoning="\n\n".join(summaries) or None,
            extra=reasoning_channel(items),
        ),
        usage=(
            usage_of(response.usage)
            if response.usage
            else Usage(input_tokens=0, output_tokens=0)
        ),
        model=response.model,
        stop_reason=stop_reason,
    )
