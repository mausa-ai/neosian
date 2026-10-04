"""Current Claude requests through the real SDK, with an in-process transport."""

import json
from typing import Any

import httpx2 as httpx
import pytest
from anthropic import AsyncAnthropic

from neosian import (
    Message,
    Model,
    ReasoningEffort,
    Role,
    ToolChoice,
    UnsupportedParameterError,
)
from neosian._foundation.llm.anthropic import AnthropicClient
from neosian._foundation.llm.anthropic_convert import convert_messages

pytestmark = pytest.mark.unit
MODELS = (Model.CLAUDE_SONNET_5_5, Model.CLAUDE_OPUS_5_5, Model.CLAUDE_FABLE_5_1)


def _reply(request: httpx.Request) -> httpx.Response:
    model = json.loads(request.content)["model"]
    frames: list[dict[str, Any]] = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 5, "output_tokens": 0},
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "thinking",
                "thinking": "",
                "signature": "",
            },
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {
                "type": "thinking_delta",
                "thinking": "Working on the request.",
            },
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {
                "type": "signature_delta",
                "signature": "signed-opaque-data",
            },
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "text_delta", "text": "OK"},
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 8},
        },
        {"type": "message_stop"},
    ]
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text="".join(
            f"event: {frame['type']}\ndata: {json.dumps(frame)}\n\n" for frame in frames
        ),
    )


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("compaction", [False, True])
async def test_binding_and_display_survive_sdk_and_replay(
    model: Model,
    streaming: bool,
    compaction: bool,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _reply(request)

    client = AnthropicClient(api_key="test")
    await client.close()
    client._client = AsyncAnthropic(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    messages = [
        Message(role=Role.SYSTEM, content="original prefix"),
        Message(role=Role.USER, content="hello"),
    ]
    try:
        if streaming:
            chunks = [
                c
                async for c in client.stream(
                    messages,
                    model,
                    reasoning_effort=ReasoningEffort.XHIGH,
                    server_compaction=compaction,
                )
            ]
            assert (
                "".join(c.reasoning or "" for c in chunks) == "Working on the request."
            )
            reply = Message(role=Role.ASSISTANT, content="OK", extra=chunks[-1].extra)
        else:
            response = await client.complete(
                messages, model, server_compaction=compaction
            )
            assert response.message.reasoning == "Working on the request."
            reply = response.message
        body = json.loads(requests[0].content)
        assert body["thinking"] == {
            "type": "adaptive",
            "display": "summarized",
            "block_binding": {"prefix_mismatch_behavior": "drop_block"},
        }
        if streaming:
            assert body["output_config"]["effort"] == "xhigh"
        else:
            assert "output_config" not in body
        assert (
            "thinking-binding-controls-2026-08-01"
            in requests[0].headers["anthropic-beta"]
        )
        assert (
            "compact-2026-01-12" in requests[0].headers["anthropic-beta"]
        ) is compaction
        assert ("context_management" in body) is compaction
        # A memory injection changes the prefix; only the provider may drop
        # a bound block, and the client requests that behavior explicitly.
        messages[0] = Message(role=Role.SYSTEM, content="updated memory")
        messages.extend([reply, Message(role=Role.USER, content="continue")])
        await client.complete(messages, model)
        replay = json.loads(requests[1].content)["messages"][1]["content"][0]
        assert replay == {
            "type": "thinking",
            "thinking": "Working on the request.",
            "signature": "signed-opaque-data",
        }
        assert convert_messages([reply])[1][0]["content"][0] == replay
    finally:
        await client.close()


@pytest.mark.parametrize("model", MODELS)
async def test_forced_tool_choice_fails_before_the_sdk(model: Model) -> None:
    client = AnthropicClient(api_key="test")
    try:
        with pytest.raises(UnsupportedParameterError, match="forced tool choice"):
            await client.complete(
                [Message(role=Role.USER, content="x")],
                model,
                tool_choice=ToolChoice.required(),
            )
    finally:
        await client.close()
