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
import cerebras.cloud.sdk.types.chat.chat_completion as cb
import openai.types.chat.chat_completion as oc
import openai.types.chat.chat_completion_chunk as ok
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

_cb_details = cb.ChatCompletionResponseUsagePromptTokensDetails(cached_tokens=0)
_cb_usage = cb.ChatCompletionResponseUsage(
    prompt_tokens=1,
    completion_tokens=1,
    total_tokens=2,
    prompt_tokens_details=_cb_details,
)
_cb_choice = cb.ChatCompletionResponseChoice(
    index=0,
    message=cb.ChatCompletionResponseChoiceMessage(role="assistant", content=""),
    finish_reason="stop",
)

CEREBRAS: dict[str, Any] = {
    "usage": _cb_usage,
    "details": _cb_details,
    "choice": _cb_choice,
    "completion": cb.ChatCompletionResponse(
        id="c",
        choices=[_cb_choice],
        created=0,
        model="m",
        object="chat.completion",
        system_fingerprint="",
        usage=_cb_usage,
    ),
}
