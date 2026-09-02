"""The candidate lanes' pacing (tests/external/pacing.py), keylessly."""

import time

import pytest

from neosian import Model
from neosian._foundation.llm.base import Message, Role, text_of
from neosian._foundation.shared.exceptions import ProviderError
from neosian.fake import FakeClient, FakeScript, FakeTurn
from tests.external.candidates import KIMI, XAI
from tests.external.pacing import PacedClient, Pacer

_ASK = [Message(role=Role.USER, content="hi")]


class _Status(Exception):
    """What the wrap reads a provider SDK's failure as (`status_code`)."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


def _failing(*statuses: int, then: str = "ok") -> FakeClient:
    turns = tuple(FakeTurn(error=_Status(s)) for s in statuses)
    return FakeClient(FakeScript(turns=(*turns, FakeTurn(content=then))))


@pytest.mark.unit
async def test_requests_start_one_interval_apart() -> None:
    pacer = Pacer(requests_per_minute=1200)  # 55 ms with the margin
    first = PacedClient(FakeClient(), pacer)
    second = PacedClient(FakeClient(), pacer)  # one clock, however many clients
    started = time.monotonic()
    await first.complete(_ASK, Model.FAKE)
    await second.complete(_ASK, Model.FAKE)
    await first.complete(_ASK, Model.FAKE)
    assert time.monotonic() - started >= 0.1


@pytest.mark.unit
async def test_a_429_waits_one_interval_and_retries() -> None:
    inner = _failing(429)
    client = PacedClient(inner, Pacer(requests_per_minute=6000))
    response = await client.complete(_ASK, Model.FAKE)
    assert text_of(response.message) == "ok"
    assert len(inner.calls) == 2


@pytest.mark.unit
async def test_a_persistent_429_gives_up_after_six() -> None:
    inner = _failing(429, 429, 429, 429, 429, 429)
    with pytest.raises(ProviderError) as info:
        await PacedClient(inner, Pacer(requests_per_minute=6000)).complete(
            _ASK, Model.FAKE
        )
    assert info.value.status == 429
    assert len(inner.calls) == 6


@pytest.mark.unit
async def test_other_failures_raise_at_once() -> None:
    inner = _failing(500)
    with pytest.raises(ProviderError) as info:
        await PacedClient(inner, Pacer(requests_per_minute=6000)).complete(
            _ASK, Model.FAKE
        )
    assert info.value.status == 500
    assert len(inner.calls) == 1


@pytest.mark.unit
async def test_streams_are_paced_and_close_passes_through() -> None:
    inner = FakeClient(FakeScript(turns=(FakeTurn(content="streamed"),)))
    client = PacedClient(inner, Pacer(requests_per_minute=6000))
    chunks = [chunk async for chunk in client.stream(_ASK, Model.FAKE)]
    assert "".join(chunk.content or "" for chunk in chunks) == "streamed"
    await client.close()
    assert inner.closed


@pytest.mark.unit
def test_of_is_one_clock_per_door() -> None:
    assert Pacer.of(XAI) is None  # no tier stated, no pacing
    kimi = Pacer.of(KIMI)
    assert kimi is not None and kimi is Pacer.of(KIMI)
    assert kimi.interval == 22.0  # 60 / 3, plus the tenth


@pytest.mark.unit
async def test_a_stream_429_before_the_first_chunk_retries() -> None:
    inner = _failing(429, then="ok")
    client = PacedClient(inner, Pacer(requests_per_minute=6000))
    chunks = [chunk async for chunk in client.stream(_ASK, Model.FAKE)]
    assert "".join(chunk.content or "" for chunk in chunks) == "ok"
    assert len(inner.calls) == 2


@pytest.mark.unit
async def test_a_stream_429_after_a_chunk_raises() -> None:
    inner = FakeClient(
        FakeScript(
            turns=(
                FakeTurn(content="partial", error=_Status(429), error_after_chunks=1),
                FakeTurn(content="never"),
            )
        )
    )
    client = PacedClient(inner, Pacer(requests_per_minute=6000))
    with pytest.raises(ProviderError):
        _ = [chunk async for chunk in client.stream(_ASK, Model.FAKE)]
    assert len(inner.calls) == 1
