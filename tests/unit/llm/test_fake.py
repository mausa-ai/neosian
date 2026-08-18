"""FakeClient determinism, scripting, and failure injection (ECOSYSTEM §7)."""

import pytest

from neosian._foundation.llm.base import Message, Role, StreamChunk, ToolCall, Usage
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn, StreamShape
from neosian._foundation.shared.exceptions import (
    ERROR_CODES,
    ContextWindowExceededError,
    FakeScriptExhaustedError,
    ProviderError,
)
from neosian._foundation.shared.types import Model, ToolCallId, ToolName

_USER = [Message(role=Role.USER, content="Hi")]


async def _collect(client: FakeClient, **kwargs: object) -> list[StreamChunk]:
    chunks = []
    async for chunk in client.stream(messages=_USER, model=Model.FAKE, **kwargs):  # type: ignore[arg-type]
        chunks.append(chunk)
    return chunks


@pytest.mark.unit
class TestFakeClientComplete:
    async def test_canned_default_repeats_identically(self) -> None:
        client = FakeClient()
        first = await client.complete(messages=_USER, model=Model.FAKE)
        second = await client.complete(messages=_USER, model=Model.FAKE)
        assert first.message.content == second.message.content == "fake response"
        assert first.usage == second.usage == Usage(input_tokens=10, output_tokens=5)

    async def test_turn_maps_field_for_field(self) -> None:
        call = ToolCall(id=ToolCallId("c1"), name=ToolName("greet"), arguments={})
        turn = FakeTurn(
            content="hello",
            reasoning="thinking",
            tool_calls=(call,),
            usage=Usage(input_tokens=7, output_tokens=3),
        )
        client = FakeClient(FakeScript(turns=(turn,)))
        response = await client.complete(messages=_USER, model=Model.FAKE)
        assert response.message.content == "hello"
        assert response.message.reasoning == "thinking"
        assert response.message.tool_calls == [call]
        assert response.usage == Usage(input_tokens=7, output_tokens=3)
        assert response.model == Model.FAKE.value
        assert response.stop_reason == "tool_calls"

    async def test_stop_reason_derivation_and_override(self) -> None:
        client = FakeClient(
            FakeScript(
                turns=(
                    FakeTurn(content="a"),
                    FakeTurn(content="b", stop_reason="max_tokens"),
                )
            )
        )
        assert (
            await client.complete(messages=_USER, model=Model.FAKE)
        ).stop_reason == "stop"
        assert (
            await client.complete(messages=_USER, model=Model.FAKE)
        ).stop_reason == "max_tokens"

    async def test_exhaustion_raises_unless_repeat_last(self) -> None:
        script = FakeScript(turns=(FakeTurn(content="only"),))
        client = FakeClient(script)
        await client.complete(messages=_USER, model=Model.FAKE)
        with pytest.raises(FakeScriptExhaustedError) as exc_info:
            await client.complete(messages=_USER, model=Model.FAKE)
        assert exc_info.value.consumed == 1

        repeating = FakeClient(
            FakeScript(turns=(FakeTurn(content="only"),), repeat_last=True)
        )
        for _ in range(3):
            response = await repeating.complete(messages=_USER, model=Model.FAKE)
            assert response.message.content == "only"

    async def test_error_injection_wraps_like_a_real_provider(self) -> None:
        original = TimeoutError("boom")
        client = FakeClient(FakeScript(turns=(FakeTurn(error=original),)))
        with pytest.raises(ProviderError) as exc_info:
            await client.complete(messages=_USER, model=Model.FAKE)
        assert exc_info.value.provider == "fake"
        assert exc_info.value.retryable is True
        assert exc_info.value.__cause__ is original

    async def test_injected_neosian_error_passes_through(self) -> None:
        original = ContextWindowExceededError("fake", context_window=128_000)
        client = FakeClient(FakeScript(turns=(FakeTurn(error=original),)))
        with pytest.raises(ContextWindowExceededError):
            await client.complete(messages=_USER, model=Model.FAKE)


@pytest.mark.unit
class TestFakeClientStream:
    async def test_same_script_is_deterministic(self) -> None:
        script = FakeScript(
            turns=(FakeTurn(content="0123456789" * 4, reasoning="why"),),
            chunk_chars=16,
        )
        first = await _collect(FakeClient(script))
        second = await _collect(FakeClient(script))
        assert [(c.content, c.reasoning, c.finish_reason, c.usage) for c in first] == [
            (c.content, c.reasoning, c.finish_reason, c.usage) for c in second
        ]

    async def test_chunk_chars_slicing(self) -> None:
        script = FakeScript(turns=(FakeTurn(content="a" * 40),), chunk_chars=16)
        chunks = await _collect(FakeClient(script))
        contents = [c.content for c in chunks if c.content is not None]
        assert contents == ["a" * 16, "a" * 16, "a" * 8]

        whole = FakeScript(turns=(FakeTurn(content="a" * 40),), chunk_chars=0)
        contents = [c.content for c in await _collect(FakeClient(whole)) if c.content]
        assert contents == ["a" * 40]

    async def test_openai_shape_trails_usage(self) -> None:
        usage = Usage(input_tokens=9, output_tokens=4)
        script = FakeScript(
            turns=(FakeTurn(content="hi", usage=usage),),
            stream_shape=StreamShape.OPENAI,
        )
        chunks = await _collect(FakeClient(script))
        assert chunks[-2].finish_reason == "stop"
        assert chunks[-2].usage is None
        assert chunks[-1].usage == usage

    async def test_anthropic_shape_leads_partial_usage(self) -> None:
        usage = Usage(input_tokens=9, output_tokens=4, cache_read_tokens=2)
        script = FakeScript(
            turns=(FakeTurn(content="hi", usage=usage),),
            stream_shape=StreamShape.ANTHROPIC,
        )
        chunks = await _collect(FakeClient(script))
        assert chunks[0].usage == Usage(
            input_tokens=9, output_tokens=0, cache_read_tokens=2
        )
        assert chunks[-1].finish_reason == "stop"
        assert chunks[-1].usage == usage

    @pytest.mark.parametrize("shape", [StreamShape.OPENAI, StreamShape.ANTHROPIC])
    async def test_last_wins_usage_equals_turn_usage(self, shape: StreamShape) -> None:
        """Under both shapes, the final usage chunk carries the turn total."""
        usage = Usage(input_tokens=100, output_tokens=20, cache_read_tokens=5)
        script = FakeScript(
            turns=(FakeTurn(content="hi", usage=usage),), stream_shape=shape
        )
        chunks = await _collect(FakeClient(script))
        last_usage = [c.usage for c in chunks if c.usage is not None][-1]
        assert last_usage == usage

    async def test_error_after_chunks(self) -> None:
        original = ConnectionError("mid-stream drop")
        script = FakeScript(
            turns=(FakeTurn(content="a" * 48, error=original, error_after_chunks=2),),
            chunk_chars=16,
        )
        received = []
        with pytest.raises(ProviderError) as exc_info:
            async for chunk in FakeClient(script).stream(
                messages=_USER, model=Model.FAKE
            ):
                received.append(chunk)
        assert len(received) == 2
        assert exc_info.value.__cause__ is original


@pytest.mark.unit
class TestFakeClientRecording:
    async def test_records_every_parameter(self) -> None:
        client = FakeClient()
        await client.complete(
            messages=_USER,
            model=Model.FAKE,
            temperature=0.5,
            max_tokens=1234,
            cache_conversation=False,
        )
        call = client.calls[0]
        assert call.model is Model.FAKE
        assert call.temperature == 0.5
        assert call.max_tokens == 1234
        assert call.cache_conversation is False
        assert call.stream is False

        async for _ in client.stream(messages=_USER, model=Model.FAKE):
            pass
        assert client.calls[1].stream is True

    async def test_message_snapshot_survives_caller_mutation(self) -> None:
        client = FakeClient()
        messages = [Message(role=Role.USER, content="first")]
        await client.complete(messages=messages, model=Model.FAKE)
        messages.append(Message(role=Role.TOOL, content="later"))
        assert len(client.calls[0].messages) == 1

    async def test_close_sets_closed(self) -> None:
        client = FakeClient()
        assert client.closed is False
        await client.close()
        assert client.closed is True

    def test_exhausted_error_is_registered(self) -> None:
        assert ERROR_CODES["llm_fake_script_exhausted"] is FakeScriptExhaustedError
