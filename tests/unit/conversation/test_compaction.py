"""The compaction boundary (DESIGN §9.6): config, trigger, deterministic
projection, batched distillation, epoch folding, usage merging. Zero keys —
the model calls ride a scripted FakeClient through the acquire seam."""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from neosian import Model, Provider
from neosian._foundation.conversation.compaction import (
    CompactionConfig,
    merge_usage,
    run_boundary,
    should_compact,
)
from neosian._foundation.conversation.distill import (
    DigestBatch,
    DigestLine,
    EpochBatch,
    EpochSummary,
)
from neosian._foundation.conversation.types import (
    ConversationProjection,
    ConversationTurn,
)
from neosian._foundation.llm.base import Message, ModelUsage, Role, Usage
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.file import FileStore
from neosian._foundation.shared.context_policy import ContextPolicy
from neosian._foundation.shared.exceptions import ConfigurationError

_CONVERSATION = "compaction-test"
_USAGE = Usage(input_tokens=100, output_tokens=10)


def _exchange(number: int, user: str = "hi", agent: str = "ok") -> ConversationTurn:
    return ConversationTurn(
        conversation_id=_CONVERSATION,
        turn=number,
        messages=(
            Message(role=Role.USER, content=user),
            Message(role=Role.ASSISTANT, content=agent),
        ),
        created_at=datetime(2026, 8, 19, tzinfo=UTC),
    )


def _acquire(fake: FakeClient) -> Callable[[Provider], FakeClient]:
    return lambda _provider: fake


def _refuse(_provider: object) -> FakeClient:
    raise AssertionError("the boundary must not call the model here")


@pytest.mark.unit
class TestCompactionConfig:
    def test_defaults(self) -> None:
        config = CompactionConfig()
        assert config.enabled is True
        assert config.model is None
        assert config.hot_turns == 8
        assert config.trigger_fraction == 0.75
        assert config.digest_chars == 200
        assert config.epoch_turns == 20
        assert config.recall_tool is True
        assert config.user_chars == 800

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"hot_turns": 0},
            {"epoch_turns": 0},
            {"digest_chars": 0},
            {"trigger_fraction": 0.0},
            {"trigger_fraction": 1.5},
        ],
    )
    def test_invalid_fields_raise(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(ConfigurationError):
            CompactionConfig(**kwargs)  # type: ignore[arg-type]


@pytest.mark.unit
class TestShouldCompact:
    def test_under_and_over_the_high_water_fraction(self) -> None:
        policy = ContextPolicy(chars_per_token=1)
        small = [Message(role=Role.USER, content="x" * 100)]
        big = [Message(role=Role.USER, content="x" * 10_000)]
        assert not should_compact(
            small, policy=policy, model=Model.FAKE_SMALL, fraction=0.75
        )
        assert should_compact(big, policy=policy, model=Model.FAKE_SMALL, fraction=0.75)


@pytest.mark.unit
class TestRunBoundary:
    async def test_deterministic_only_never_calls_the_model(
        self, store: FileStore
    ) -> None:
        turns = [_exchange(n) for n in range(1, 5)]
        result = await run_boundary(
            store=store,
            conversation_id=_CONVERSATION,
            turns=turns,
            projections=(),
            config=CompactionConfig(hot_turns=2),
            model=Model.FAKE,
            acquire=_refuse,
        )
        assert [e.turn for e in result.entries] == [1, 2]
        assert all(e.kind == "log" for e in result.entries)
        assert result.usage is None
        stored = await store.read_projections(_CONVERSATION)
        assert stored == result.entries

    async def test_hot_band_stays_unprojected(self, store: FileStore) -> None:
        turns = [_exchange(n) for n in range(1, 11)]
        result = await run_boundary(
            store=store,
            conversation_id=_CONVERSATION,
            turns=turns,
            projections=(),
            config=CompactionConfig(hot_turns=8),
            model=Model.FAKE,
            acquire=_refuse,
        )
        assert [e.turn for e in result.entries] == [1, 2]

    async def test_no_op_when_everything_is_covered(self, store: FileStore) -> None:
        turns = [_exchange(n) for n in range(1, 5)]
        existing = (
            ConversationProjection(turn=1, kind="log", text="one"),
            ConversationProjection(turn=2, kind="log", text="two"),
        )
        result = await run_boundary(
            store=store,
            conversation_id=_CONVERSATION,
            turns=turns,
            projections=existing,
            config=CompactionConfig(hot_turns=2),
            model=Model.FAKE,
            acquire=_refuse,
        )
        assert result.entries == ()
        assert await store.read_projections(_CONVERSATION) == ()

    async def test_distillation_produces_digest_entries(self, store: FileStore) -> None:
        prose = "decision: we ship on Friday. " * 20
        turns = [_exchange(1, agent=prose), _exchange(2), _exchange(3)]
        batch = DigestBatch(lines=[DigestLine(turn=1, line="shipped on Friday")])
        fake = FakeClient(
            FakeScript(turns=(FakeTurn(content=batch.model_dump_json(), usage=_USAGE),))
        )
        result = await run_boundary(
            store=store,
            conversation_id=_CONVERSATION,
            turns=turns,
            projections=(),
            config=CompactionConfig(hot_turns=2, digest_chars=100),
            model=Model.FAKE,
            acquire=_acquire(fake),
        )
        call = fake.calls[-1]
        assert call.response_format is not None
        assert call.response_format.schema is DigestBatch
        assert call.cache_conversation is False
        assert call.model is Model.FAKE
        (entry,) = result.entries
        assert entry.kind == "digest"
        assert "AGENT: shipped on Friday" in entry.text
        assert prose not in entry.text
        assert result.usage == _USAGE
        assert result.model == Model.FAKE.value

    async def test_boundary_does_not_close_the_acquired_client(
        self, store: FileStore
    ) -> None:
        """`acquire` is a lease (ledger #33): the caller owns the client's
        lifetime — closing it here would kill a session-cached client."""
        prose = "decision: we ship on Friday. " * 20
        turns = [_exchange(1, agent=prose), _exchange(2), _exchange(3)]
        batch = DigestBatch(lines=[DigestLine(turn=1, line="shipped on Friday")])
        fake = FakeClient(
            FakeScript(turns=(FakeTurn(content=batch.model_dump_json(), usage=_USAGE),))
        )
        await run_boundary(
            store=store,
            conversation_id=_CONVERSATION,
            turns=turns,
            projections=(),
            config=CompactionConfig(hot_turns=2, digest_chars=100),
            model=Model.FAKE,
            acquire=_acquire(fake),
        )
        assert fake.closed is False

    async def test_partial_digest_falls_back_by_turn_number(
        self, store: FileStore
    ) -> None:
        long = "long prose " * 30
        turns = [_exchange(1, agent=long), _exchange(2, agent=long), _exchange(3)]
        batch = DigestBatch(lines=[DigestLine(turn=2, line="only two came back")])
        fake = FakeClient(
            FakeScript(turns=(FakeTurn(content=batch.model_dump_json()),))
        )
        result = await run_boundary(
            store=store,
            conversation_id=_CONVERSATION,
            turns=turns,
            projections=(),
            config=CompactionConfig(hot_turns=1, digest_chars=100),
            model=Model.FAKE,
            acquire=_acquire(fake),
        )
        by_turn = {e.turn: e for e in result.entries}
        assert by_turn[1].kind == "log"
        assert by_turn[2].kind == "digest"
        assert "only two came back" in by_turn[2].text

    async def test_distillation_failure_degrades_to_log(self, store: FileStore) -> None:
        turns = [_exchange(1, agent="long prose " * 30), _exchange(2), _exchange(3)]
        fake = FakeClient(
            FakeScript(turns=(FakeTurn(error=RuntimeError("model down")),))
        )
        result = await run_boundary(
            store=store,
            conversation_id=_CONVERSATION,
            turns=turns,
            projections=(),
            config=CompactionConfig(hot_turns=2, digest_chars=100),
            model=Model.FAKE,
            acquire=_acquire(fake),
        )
        (entry,) = result.entries
        assert entry.kind == "log"
        assert result.usage is None
        assert await store.read_projections(_CONVERSATION) == result.entries

    async def test_epoch_fold_is_model_written_and_supersedes(
        self, store: FileStore
    ) -> None:
        turns = [_exchange(n) for n in range(1, 5)]
        batch = EpochBatch(
            epochs=[EpochSummary(last_turn=2, summary="the opening epoch")]
        )
        fake = FakeClient(
            FakeScript(turns=(FakeTurn(content=batch.model_dump_json(), usage=_USAGE),))
        )
        result = await run_boundary(
            store=store,
            conversation_id=_CONVERSATION,
            turns=turns,
            projections=(),
            config=CompactionConfig(hot_turns=1, epoch_turns=2),
            model=Model.FAKE,
            acquire=_acquire(fake),
        )
        call = fake.calls[-1]
        assert call.response_format is not None
        assert call.response_format.schema is EpochBatch
        epochs = [e for e in result.entries if e.kind == "epoch"]
        assert epochs == [
            ConversationProjection(
                turn=2, kind="epoch", text="the opening epoch", span=2
            )
        ]
        assert result.usage == _USAGE
        # Only the [1-2] block is due: [3-4] reaches past the cutoff (3).
        assert len(epochs) == 1

    async def test_epoch_fold_is_not_duplicated_on_the_next_boundary(
        self, store: FileStore
    ) -> None:
        turns = [_exchange(n) for n in range(1, 5)]
        first = (
            ConversationProjection(turn=1, kind="log", text="one"),
            ConversationProjection(turn=2, kind="log", text="two"),
            ConversationProjection(turn=3, kind="log", text="three"),
            ConversationProjection(turn=2, kind="epoch", text="fold", span=2),
        )
        result = await run_boundary(
            store=store,
            conversation_id=_CONVERSATION,
            turns=turns,
            projections=first,
            config=CompactionConfig(hot_turns=1, epoch_turns=2),
            model=Model.FAKE,
            acquire=_refuse,
        )
        assert result.entries == ()

    async def test_epoch_failure_skips_the_fold_and_keeps_log_entries(
        self, store: FileStore
    ) -> None:
        turns = [_exchange(n) for n in range(1, 5)]
        fake = FakeClient(
            FakeScript(turns=(FakeTurn(error=RuntimeError("model down")),))
        )
        result = await run_boundary(
            store=store,
            conversation_id=_CONVERSATION,
            turns=turns,
            projections=(),
            config=CompactionConfig(hot_turns=1, epoch_turns=2),
            model=Model.FAKE,
            acquire=_acquire(fake),
        )
        assert [e.kind for e in result.entries] == ["log", "log", "log"]
        # The block stays unfolded — retried at the next boundary.
        assert all(e.kind != "epoch" for e in result.entries)


@pytest.mark.unit
class TestMergeUsage:
    def test_appends_a_new_model(self) -> None:
        existing = (ModelUsage(model="fake-model", usage=_USAGE),)
        merged = merge_usage(existing, "other-model", _USAGE)
        assert merged == (
            ModelUsage(model="fake-model", usage=_USAGE),
            ModelUsage(model="other-model", usage=_USAGE),
        )

    def test_sums_an_existing_model(self) -> None:
        existing = (ModelUsage(model="fake-model", usage=_USAGE),)
        merged = merge_usage(existing, "fake-model", _USAGE)
        assert merged == (ModelUsage(model="fake-model", usage=_USAGE + _USAGE),)
