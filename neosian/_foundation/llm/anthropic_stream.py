"""Reading Anthropic's wire — the final message and the event stream — a
pure move out of `anthropic.py` (NC7, REVIEW LL-31, LL-32).

`parse_message` is typed on the SDK's message union; the event stream is
read duck-typed, because the GA and compaction-beta stream managers join
to an unusable type and their events differ only in module.
"""

from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

from anthropic.types import Message as AnthropicMessage
from anthropic.types.beta import BetaCompactionIterationUsage, BetaMessage

from neosian._foundation.llm.base import (
    CompactionBlock,
    CompletionResponse,
    ContentBlock,
    Message,
    Role,
    StreamChunk,
    TextBlock,
    ToolCall,
    Usage,
)
from neosian._foundation.llm.errors import tool_arguments
from neosian._foundation.shared.types import ToolCallId, ToolName


def compaction_usage(usage: object) -> Usage:
    """Compaction-iteration token spend on an API usage object.

    The compact beta reports summarization tokens only under
    usage.iterations — never in the top-level counts — so they are folded
    into the Usage neosian reports; hidden spend would break the
    cost-visibility promise (ledger #29's philosophy). `iterations` exists
    on the beta usage types only (BetaUsage, BetaMessageDeltaUsage).
    """
    total = Usage(input_tokens=0, output_tokens=0)
    for iteration in getattr(usage, "iterations", None) or []:
        if isinstance(iteration, BetaCompactionIterationUsage):
            total = total + Usage(
                input_tokens=iteration.input_tokens,
                output_tokens=iteration.output_tokens,
                cache_read_tokens=iteration.cache_read_input_tokens,
                cache_write_tokens=iteration.cache_creation_input_tokens,
            )
    return total


def thinking_extra(blocks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The provider channel for a turn's thinking blocks — verbatim, with
    their signatures, so the next turn can echo them (LL-1, NF #172)."""
    return {"anthropic": {"thinking_blocks": blocks}} if blocks else None


def parse_message(response: AnthropicMessage | BetaMessage) -> CompletionResponse:
    """Read one final message — GA or compaction-beta — into a
    CompletionResponse."""
    # Build text content, reasoning, and tool calls from content blocks.
    # Compaction blocks (server-side compaction beta) are collected in
    # provider order; when any exist, content becomes an ordered block
    # list the caller echoes back — otherwise the plain-string shape is
    # byte-identical to the flag-off path.
    text_content = ""
    reasoning_content = ""
    tool_calls: list[ToolCall] = []
    ordered_blocks: list[ContentBlock] = []
    thinking_blocks: list[dict[str, Any]] = []
    saw_compaction = False

    for block in response.content:
        if block.type == "thinking":
            reasoning_content += block.thinking
            thinking_blocks.append(
                {
                    "type": "thinking",
                    "thinking": block.thinking,
                    "signature": block.signature,
                }
            )
        elif block.type == "redacted_thinking":
            # Encrypted: unreadable here, echoed back verbatim.
            thinking_blocks.append({"type": "redacted_thinking", "data": block.data})
        elif block.type == "text":
            text_content += block.text
            ordered_blocks.append(TextBlock(text=block.text))
        elif block.type == "compaction":
            saw_compaction = True
            ordered_blocks.append(
                CompactionBlock(
                    content=getattr(block, "content", None),
                    encrypted_content=getattr(block, "encrypted_content", None),
                )
            )
        elif block.type == "tool_use":
            tool_calls.append(
                ToolCall(
                    id=ToolCallId(block.id),
                    name=ToolName(block.name),
                    arguments=(
                        dict(block.input) if isinstance(block.input, dict) else {}
                    ),
                )
            )

    content: str | list[ContentBlock] | None
    if saw_compaction:
        content = ordered_blocks
    else:
        content = text_content if text_content else None

    return CompletionResponse(
        message=Message(
            role=Role.ASSISTANT,
            content=content,
            reasoning=reasoning_content if reasoning_content else None,
            tool_calls=tool_calls,
            extra=thinking_extra(thinking_blocks),
        ),
        usage=Usage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read_tokens=response.usage.cache_read_input_tokens or 0,
            cache_write_tokens=response.usage.cache_creation_input_tokens or 0,
        )
        + compaction_usage(response.usage),
        model=response.model,
        stop_reason=response.stop_reason,
    )


async def iter_chunks(events: AsyncIterator[Any]) -> AsyncGenerator[StreamChunk]:
    """Read one message stream's events into StreamChunks.

    Usage is last-wins for the consumer: a partial chunk (input and cache
    tokens) leaves at message_start, the complete usage rides the terminal
    chunk. Tool calls, compaction blocks and thinking blocks are assembled
    whole and yielded on the terminal chunk.
    """
    # Usage tracking: input/cache from message_start, output from message_delta
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    # Compaction spend rides usage.iterations; snapshots are
    # cumulative, so each sighting replaces (never adds to)
    # the previous one.
    compaction_spend = Usage(input_tokens=0, output_tokens=0)

    # Tool call accumulation state: (id, name, raw input),
    # decoded at message_stop once the stop reason is known.
    pending_tools: list[tuple[str, str, str]] = []
    current_tool_id: str | None = None
    current_tool_name: str | None = None
    current_tool_input: str = ""

    # Compaction block accumulation (compaction_delta carries
    # the FULL value — assignment, never concatenation)
    compaction_blocks: list[CompactionBlock] = []
    current_compaction: CompactionBlock | None = None

    # Thinking blocks, kept whole with their signatures for
    # the echo-back (LL-1): the delta appends, the signature
    # arrives last in its own delta.
    thinking_blocks: list[dict[str, Any]] = []
    current_thinking: dict[str, Any] | None = None

    # Real stop reason from the API (reported in message_delta)
    stop_reason: str | None = None

    # API-reported model string (reported in message_start)
    api_model: str | None = None

    async for event in events:
        if event.type == "message_start":
            if hasattr(event, "message"):
                api_model = getattr(event.message, "model", None)
            # Input and cache tokens are reported in message_start
            if hasattr(event, "message") and hasattr(event.message, "usage"):
                msg_usage = event.message.usage
                input_tokens = getattr(msg_usage, "input_tokens", 0) or 0
                cache_creation_tokens = (
                    getattr(msg_usage, "cache_creation_input_tokens", 0) or 0
                )
                cache_read_tokens = (
                    getattr(msg_usage, "cache_read_input_tokens", 0) or 0
                )
                found = compaction_usage(msg_usage)
                if found.total_tokens:
                    compaction_spend = found
                # Surface the partial usage immediately so consumers
                # interrupted mid-stream (guard block, error) can meter
                # the input/cache tokens already billed. The complete
                # usage on the message_stop chunk supersedes this one —
                # consumers must treat per-stream usage as last-wins.
                yield StreamChunk(
                    usage=Usage(
                        input_tokens=input_tokens,
                        output_tokens=0,
                        cache_read_tokens=cache_read_tokens,
                        cache_write_tokens=cache_creation_tokens,
                    ),
                    model=api_model,
                )

        elif event.type == "content_block_start":
            block = event.content_block
            if getattr(block, "type", None) == "tool_use":
                current_tool_id = block.id
                current_tool_name = block.name
                current_tool_input = ""
            elif getattr(block, "type", None) == "compaction":
                current_compaction = CompactionBlock(
                    content=getattr(block, "content", None),
                    encrypted_content=getattr(block, "encrypted_content", None),
                )
            elif getattr(block, "type", None) == "thinking":
                current_thinking = {
                    "type": "thinking",
                    "thinking": "",
                    "signature": "",
                }
            elif getattr(block, "type", None) == "redacted_thinking":
                thinking_blocks.append(
                    {"type": "redacted_thinking", "data": block.data}
                )

        elif event.type == "content_block_delta":
            delta_type = getattr(event.delta, "type", None)
            if delta_type == "thinking_delta":
                if current_thinking is not None:
                    current_thinking["thinking"] += event.delta.thinking
                yield StreamChunk(
                    reasoning=event.delta.thinking,
                    model=api_model,
                )
            elif delta_type == "signature_delta":
                if current_thinking is not None:
                    current_thinking["signature"] = event.delta.signature
            elif delta_type == "text_delta":
                yield StreamChunk(
                    content=event.delta.text,
                    model=api_model,
                )
            elif delta_type == "input_json_delta":
                current_tool_input += event.delta.partial_json
            elif delta_type == "compaction_delta" and current_compaction is not None:
                # The delta carries the FULL summary (the SDK
                # accumulator assigns, never appends) — += here
                # would duplicate content.
                current_compaction.content = getattr(event.delta, "content", None)

        elif event.type == "content_block_stop":
            if current_tool_id is not None:
                pending_tools.append(
                    (
                        current_tool_id,
                        current_tool_name or "",
                        current_tool_input,
                    )
                )
                current_tool_id = None
                current_tool_name = None
                current_tool_input = ""
            elif current_compaction is not None:
                compaction_blocks.append(current_compaction)
                current_compaction = None
            elif current_thinking is not None:
                thinking_blocks.append(current_thinking)
                current_thinking = None

        elif event.type == "message_delta":
            # Output tokens are reported in message_delta
            if hasattr(event, "usage") and event.usage:
                output_tokens = event.usage.output_tokens
                found = compaction_usage(event.usage)
                if found.total_tokens:
                    compaction_spend = found
            # The API's actual stop reason (e.g. "max_tokens",
            # "end_turn", "tool_use") also arrives here.
            delta = getattr(event, "delta", None)
            delta_stop = getattr(delta, "stop_reason", None)
            if delta_stop:
                stop_reason = delta_stop

        elif event.type == "message_stop":
            # Prefer the API's stop reason; fall back to the
            # synthesized value if the event never carried one.
            finish_reason = stop_reason or ("tool_use" if pending_tools else "stop")
            tool_calls = [
                ToolCall(
                    id=ToolCallId(tool_id),
                    name=ToolName(name),
                    arguments=tool_arguments("anthropic", raw, stop_reason=stop_reason),
                )
                for tool_id, name, raw in pending_tools
            ]
            yield StreamChunk(
                finish_reason=finish_reason,
                usage=Usage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=cache_read_tokens,
                    cache_write_tokens=cache_creation_tokens,
                )
                + compaction_spend,
                tool_calls=tool_calls,
                model=api_model,
                compaction=tuple(compaction_blocks),
                extra=thinking_extra(thinking_blocks),
            )
