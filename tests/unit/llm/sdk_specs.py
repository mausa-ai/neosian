"""Real SDK instances that spec the provider test mocks (TP-10).

A mock built with ``spec_set=`` refuses every attribute the real object
lacks, read or written, so a renamed SDK field turns these suites red
instead of staying green. Pydantic models carry no field attributes on
the class, so the specs are *instances*, constructed once here.
"""

from typing import Any
from unittest.mock import create_autospec

import anthropic.types as at
import anthropic.types.beta as ab
import openai.types.chat.chat_completion as oc
import openai.types.chat.chat_completion_chunk as ok
import openai.types.responses as orp
from anthropic.types.raw_message_delta_event import Delta
from openai.types.chat import ChatCompletionMessage
from openai.types.completion_usage import CompletionUsage, PromptTokensDetails


def autospec(instance: Any) -> Any:
    """A recursively spec'd mock of a real SDK object; only its real
    fields may be read or set (`spec_set`), so a renamed field turns a
    test red at the assignment, not green by accident."""
    return create_autospec(instance, spec_set=True, instance=True)


_usage = at.Usage(
    input_tokens=1,
    output_tokens=1,
    cache_creation_input_tokens=0,
    cache_read_input_tokens=0,
)
_text = at.TextBlock(type="text", text="")
_text_delta = at.TextDelta(type="text_delta", text="")
_message = at.Message(
    id="msg",
    content=[_text],
    model="claude-sonnet-5",
    role="assistant",
    type="message",
    usage=_usage,
)

ANTHROPIC: dict[str, Any] = {
    "message": _message,
    "usage": _usage,
    # Server compaction's spend rides the beta response: `iterations`
    # exists only on BetaUsage.
    "beta_usage": ab.BetaUsage(
        input_tokens=1,
        output_tokens=1,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    ),
    "stop": Delta(),
    "text": _text,
    "tool_use": at.ToolUseBlock(type="tool_use", id="", name="", input={}),
    "thinking": at.ThinkingBlock(type="thinking", thinking="", signature=""),
    "redacted_thinking": at.RedactedThinkingBlock(type="redacted_thinking", data=""),
    "text_delta": _text_delta,
    "thinking_delta": at.ThinkingDelta(type="thinking_delta", thinking=""),
    "signature_delta": at.SignatureDelta(type="signature_delta", signature=""),
    "input_json_delta": at.InputJSONDelta(type="input_json_delta", partial_json=""),
    "message_start": at.RawMessageStartEvent(type="message_start", message=_message),
    "content_block_start": at.RawContentBlockStartEvent(
        type="content_block_start", index=0, content_block=_text
    ),
    "content_block_delta": at.RawContentBlockDeltaEvent(
        type="content_block_delta", index=0, delta=_text_delta
    ),
    "content_block_stop": at.RawContentBlockStopEvent(
        type="content_block_stop", index=0
    ),
    "message_delta": at.RawMessageDeltaEvent(
        type="message_delta", delta=Delta(), usage=at.MessageDeltaUsage(output_tokens=0)
    ),
    "message_stop": at.RawMessageStopEvent(type="message_stop"),
    "compaction": ab.BetaCompactionBlock(type="compaction"),
    "compaction_delta": ab.BetaCompactionContentBlockDelta(type="compaction_delta"),
    "compaction_iteration": ab.BetaCompactionIterationUsage(
        type="compaction",
        input_tokens=1,
        output_tokens=1,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    ),
    "message_iteration": ab.BetaMessageIterationUsage(
        type="message",
        model="claude-sonnet-5",
        input_tokens=1,
        output_tokens=1,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    ),
}

_oa_details = PromptTokensDetails(cached_tokens=0)
_oa_usage = CompletionUsage(
    prompt_tokens=1,
    completion_tokens=1,
    total_tokens=2,
    prompt_tokens_details=_oa_details,
)
_oa_choice = oc.Choice(
    index=0,
    message=ChatCompletionMessage(role="assistant", content=""),
    finish_reason="stop",
)
_oa_chunk_choice = ok.Choice(
    index=0, delta=ok.ChoiceDelta(content=""), finish_reason=None
)

OPENAI: dict[str, Any] = {
    "usage": _oa_usage,
    "details": _oa_details,
    "choice": _oa_choice,
    "completion": oc.ChatCompletion(
        id="c",
        choices=[_oa_choice],
        created=0,
        model="m",
        object="chat.completion",
        usage=_oa_usage,
    ),
    "chunk_choice": _oa_chunk_choice,
    "chunk": ok.ChatCompletionChunk(
        id="c",
        choices=[_oa_chunk_choice],
        created=0,
        model="m",
        object="chat.completion.chunk",
        usage=_oa_usage,
    ),
}

# The Responses wire (§31.5): the response, its output items, and the
# events the reader keys on.
_r_usage = orp.ResponseUsage(
    input_tokens=1,
    input_tokens_details=orp.response_usage.InputTokensDetails(
        cache_write_tokens=0, cached_tokens=0
    ),
    output_tokens=1,
    output_tokens_details=orp.response_usage.OutputTokensDetails(reasoning_tokens=0),
    total_tokens=2,
)
_r_text = orp.ResponseOutputText(annotations=[], text="", type="output_text")
_r_refusal = orp.ResponseOutputRefusal(refusal="", type="refusal")
_r_message = orp.ResponseOutputMessage(
    id="msg", content=[_r_text], role="assistant", status="completed", type="message"
)
_r_call = orp.ResponseFunctionToolCall(
    arguments="{}", call_id="call", name="f", type="function_call", id="fc"
)
_r_reasoning = orp.ResponseReasoningItem(
    id="rs",
    summary=[orp.response_reasoning_item.Summary(text="", type="summary_text")],
    type="reasoning",
    encrypted_content="",
)
_r_response = orp.Response(
    id="resp",
    created_at=0,
    model="m",
    object="response",
    output=[_r_message],
    parallel_tool_calls=True,
    tool_choice="auto",
    tools=[],
    usage=_r_usage,
)

RESPONSES: dict[str, Any] = {
    "response": _r_response,
    "usage": _r_usage,
    "message": _r_message,
    "text": _r_text,
    "refusal": _r_refusal,
    "function_call": _r_call,
    "reasoning": _r_reasoning,
    "created": orp.ResponseCreatedEvent(
        response=_r_response, sequence_number=0, type="response.created"
    ),
    "text_delta": orp.ResponseTextDeltaEvent(
        content_index=0,
        delta="",
        item_id="msg",
        logprobs=[],
        output_index=0,
        sequence_number=0,
        type="response.output_text.delta",
    ),
    "refusal_delta": orp.ResponseRefusalDeltaEvent(
        content_index=0,
        delta="",
        item_id="msg",
        output_index=0,
        sequence_number=0,
        type="response.refusal.delta",
    ),
    "summary_delta": orp.ResponseReasoningSummaryTextDeltaEvent(
        delta="",
        item_id="rs",
        output_index=0,
        sequence_number=0,
        summary_index=0,
        type="response.reasoning_summary_text.delta",
    ),
    "item_added": orp.ResponseOutputItemAddedEvent(
        item=_r_call,
        output_index=0,
        sequence_number=0,
        type="response.output_item.added",
    ),
    "item_done": orp.ResponseOutputItemDoneEvent(
        item=_r_call,
        output_index=0,
        sequence_number=0,
        type="response.output_item.done",
    ),
    "arguments_delta": orp.ResponseFunctionCallArgumentsDeltaEvent(
        delta="",
        item_id="fc",
        output_index=0,
        sequence_number=0,
        type="response.function_call_arguments.delta",
    ),
    "completed": orp.ResponseCompletedEvent(
        response=_r_response, sequence_number=0, type="response.completed"
    ),
    "incomplete": orp.ResponseIncompleteEvent(
        response=_r_response, sequence_number=0, type="response.incomplete"
    ),
    "failed": orp.ResponseFailedEvent(
        response=_r_response, sequence_number=0, type="response.failed"
    ),
    "error": orp.ResponseErrorEvent(message="", sequence_number=0, type="error"),
}
