"""Reflection — session-boundary auto-memory (DESIGN §15). Zero keys.

The engine: one structured-output call whose emitted operations execute
through the shared memory dispatcher, degrade-safe. The Conversation
wiring: explicit `reflect()`, the default-on `aclose()` rider, receipts
with spend, and the NR done-when — a conversation that never explicitly
wrote memory closes and the store holds the right facts.
"""

import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from neosian import AgentConfig, Model, Provider
from neosian._foundation.conversation.core import Conversation
from neosian._foundation.conversation.reflection import (
    CreateOp,
    DeleteOp,
    ReflectionBatch,
    ReflectionConfig,
    ReflectionOp,
    ReflectionResult,
    ReplaceOp,
    run_reflection,
)
from neosian._foundation.conversation.types import ConversationTurn
from neosian._foundation.llm.base import BaseLLMClient, Message, Role, ToolCall, Usage
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.shared.exceptions import ConfigurationError
from neosian._foundation.shared.types import (
    AnyModel,
    SystemPrompt,
    ToolCallId,
    ToolName,
)
from tests.unit.memory.conftest import ManualClock

_SYSTEM = SystemPrompt("You are a helpful agent with memory.")
_USAGE = Usage(input_tokens=100, output_tokens=10)


def _turn(number: int, user: str, agent: str) -> ConversationTurn:
    return ConversationTurn(
        conversation_id="t1",
        turn=number,
        messages=(
            Message(role=Role.USER, content=user),
            Message(role=Role.ASSISTANT, content=agent),
        ),
        created_at=datetime(2026, 8, 21, tzinfo=UTC),
    )


def _memory(tmp_path: Path, *extra_mounts: Mount) -> MemoryConfig:
    return MemoryConfig(
        store=FileStore(tmp_path / "store"),
        mounts=(Mount(scope="user:1", mount_path="memories"), *extra_mounts),
    )


def _batch(*ops: ReflectionOp) -> str:
    return ReflectionBatch(ops=list(ops)).model_dump_json()


def _create_op(path: str = "/memories/preferences") -> CreateOp:
    return CreateOp(command="create", path=path, content="Prefers espresso.")


def _scripted(*turns: FakeTurn) -> FakeClient:
    return FakeClient(FakeScript(turns=turns))


def _config(fake: FakeClient) -> AgentConfig:
    return AgentConfig(
        system_prompt=_SYSTEM,
        model=Model.FAKE,
        enable_todo=False,
        client_factory=lambda _: fake,
    )


@pytest.mark.unit
class TestReflectionWireSchema:
    def test_the_wire_schema_is_strict_compatible(self) -> None:
        """OpenAI's strict mode requires every property in `required` and
        rejects `oneOf` — the 400 the first external dispatch of the
        reflection engine surfaced, pinned keylessly."""
        from neosian._foundation.shared.schema import get_json_schema

        def check(node: object) -> None:
            if isinstance(node, dict):
                if node.get("type") == "object" and "properties" in node:
                    assert set(node["properties"]) == set(node.get("required", ()))
                assert "oneOf" not in node
                for value in node.values():
                    check(value)
            elif isinstance(node, list):
                for item in node:
                    check(item)

        check(get_json_schema(ReflectionBatch))


@pytest.mark.unit
class TestRunReflection:
    async def test_ops_land_through_the_dispatcher_with_the_actor(
        self, tmp_path: Path
    ) -> None:
        memory = _memory(tmp_path)
        fake = _scripted(FakeTurn(content=_batch(_create_op()), usage=_USAGE))
        result = await run_reflection(
            memory_config=memory,
            turns=[_turn(1, "I only drink espresso.", "Noted.")],
            acquire=lambda _: fake,
            model=Model.FAKE,
            actor="t1",
        )
        assert [(w.command, w.path, w.version) for w in result.writes] == [
            ("create", "/memories/preferences", 1)
        ]
        assert result.usage == _USAGE
        assert result.model is not None
        document = await memory.store.read("user:1", "preferences")
        assert document is not None and document.content == "Prefers espresso."
        versions = await memory.store.versions("user:1", "preferences")
        assert versions[0].actor == "t1"

    async def test_payload_shows_writable_bodies_and_the_transcript(
        self, tmp_path: Path
    ) -> None:
        memory = _memory(
            tmp_path, Mount(scope="user:1/kb:shared", mount_path="kb", read_only=True)
        )
        await memory.store.write("user:1", "stack", "Runs on Postgres 16.")
        await memory.store.write("user:1/kb:shared", "guide", "Reference only.")
        fake = _scripted(FakeTurn(content=_batch()))
        await run_reflection(
            memory_config=memory,
            turns=[_turn(1, "hello", "hi")],
            acquire=lambda _: fake,
            model=Model.FAKE,
            actor="t1",
        )
        system, payload = fake.calls[0].messages
        assert "closing out an agent session" in str(system.content)
        text = str(payload.content)
        assert "# Current memory" in text
        # Bodies and the transcript are data: each rides inside a fence
        # tagged with a per-call token the system prompt names.
        fence = re.search(r"<<<data ([0-9a-f]{16})>>>", text)
        assert fence is not None
        token = fence.group(1)
        assert f"`<<<data {token}>>>`" in str(system.content)
        assert (
            f"### /memories/stack\n<<<data {token}>>>\nRuns on Postgres 16.\n<<<end {token}>>>"
            in text
        )
        assert (
            f"# Session transcript\n\n<<<data {token}>>>\nTurn 1 (verbatim):\nUSER: hello"
            in text
        )
        # Read-only mounts take no operations, so they are not shown.
        assert "/kb" not in text
        assert "Reference only." not in text

    async def test_update_not_duplicate(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path)
        await memory.store.write("user:1", "preferences", "Prefers espresso.")
        op = ReplaceOp(
            command="str_replace",
            path="/memories/preferences",
            old_str="espresso",
            new_str="ristretto",
        )
        fake = _scripted(FakeTurn(content=_batch(op)))
        result = await run_reflection(
            memory_config=memory,
            turns=[_turn(1, "Ristretto now.", "Noted.")],
            acquire=lambda _: fake,
            model=Model.FAKE,
            actor="t1",
        )
        assert result.writes[0].version == 2
        document = await memory.store.read("user:1", "preferences")
        assert document is not None and document.content == "Prefers ristretto."
        assert len(await memory.store.list_documents("user:1")) == 1

    async def test_delete_receipt_has_no_live_version(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path)
        await memory.store.write("user:1", "stale", "Wrong fact.")
        op = DeleteOp(command="delete", path="/memories/stale")
        fake = _scripted(FakeTurn(content=_batch(op)))
        result = await run_reflection(
            memory_config=memory,
            turns=[_turn(1, "That was wrong.", "Removed.")],
            acquire=lambda _: fake,
            model=Model.FAKE,
            actor="t1",
            min_age=timedelta(0),  # just written; the floor is tested apart
        )
        assert [(w.command, w.version) for w in result.writes] == [("delete", None)]
        assert await memory.store.read("user:1", "stale") is None

    async def test_model_failure_degrades_to_empty(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        memory = _memory(tmp_path)
        fake = _scripted(FakeTurn(content="not json at all"))
        with caplog.at_level(logging.WARNING):
            result = await run_reflection(
                memory_config=memory,
                turns=[_turn(1, "hello", "hi")],
                acquire=lambda _: fake,
                model=Model.FAKE,
                actor="t1",
            )
        assert result.writes == () and result.model is None
        assert result.degraded is not None
        assert result.degraded.startswith("Reflection failed: ")
        assert "Reflection failed; degrading" in caplog.text
        assert await memory.store.list_documents("user:1") == ()

    async def test_a_configuration_error_propagates(self, tmp_path: Path) -> None:
        # The #84 rule: a missing key or an unknown model is the caller's
        # setup, never a degrade to swallow.
        def refuse(_: AnyModel) -> BaseLLMClient:
            raise ConfigurationError("no key for this provider")

        with pytest.raises(ConfigurationError, match="no key"):
            await run_reflection(
                memory_config=_memory(tmp_path),
                turns=[_turn(1, "hello", "hi")],
                acquire=refuse,
                model=Model.FAKE,
                actor="t1",
            )

    async def test_deletes_respect_the_age_floor(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The gardener's protections hold for the more frequent pass too:
        # a fresh document survives a reflection delete, an old one goes.
        clock = ManualClock(datetime(2026, 8, 1, tzinfo=UTC))
        memory = MemoryConfig(
            store=FileStore(tmp_path / "store", clock=clock),
            mounts=(Mount(scope="user:1", mount_path="memories"),),
        )
        await memory.store.write("user:1", "old", "stale claim")
        clock._now = datetime(2026, 8, 22, tzinfo=UTC)  # noqa: SLF001
        await memory.store.write("user:1", "fresh", "new today")
        fake = _scripted(
            FakeTurn(
                content=_batch(
                    DeleteOp(command="delete", path="/memories/old"),
                    DeleteOp(command="delete", path="/memories/fresh"),
                )
            )
        )
        with caplog.at_level(logging.WARNING):
            result = await run_reflection(
                memory_config=memory,
                turns=[_turn(1, "hello", "hi")],
                acquire=lambda _: fake,
                model=Model.FAKE,
                actor="t1",
                clock=clock,
            )
        assert [w.path for w in result.writes] == ["/memories/old"]
        assert "inside the age floor" in caplog.text
        assert await memory.store.read("user:1", "fresh") is not None
        assert await memory.store.read("user:1", "old") is None

    async def test_redacted_documents_take_no_operation(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        memory = _memory(tmp_path)
        await memory.store.write("user:1", "erased", "secret")
        await memory.store.redact("user:1", path="erased")
        fake = _scripted(FakeTurn(content=_batch(_create_op("/memories/erased"))))
        with caplog.at_level(logging.WARNING):
            result = await run_reflection(
                memory_config=memory,
                turns=[_turn(1, "hello", "hi")],
                acquire=lambda _: fake,
                model=Model.FAKE,
                actor="t1",
            )
        assert result.writes == ()
        assert "is redacted" in caplog.text

    async def test_failed_op_is_skipped_and_the_rest_land(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        memory = _memory(
            tmp_path, Mount(scope="user:1/kb:shared", mount_path="kb", read_only=True)
        )
        bad = CreateOp(command="create", path="/kb/note", content="nope")
        fake = _scripted(FakeTurn(content=_batch(bad, _create_op())))
        with caplog.at_level(logging.WARNING):
            result = await run_reflection(
                memory_config=memory,
                turns=[_turn(1, "hello", "hi")],
                acquire=lambda _: fake,
                model=Model.FAKE,
                actor="t1",
            )
        assert [w.path for w in result.writes] == ["/memories/preferences"]
        assert "memory_read_only_mount" in caplog.text
        assert await memory.store.read("user:1/kb:shared", "note") is None

    async def test_edit_only_mount_is_shown_annotated(self, tmp_path: Path) -> None:
        memory = _memory(
            tmp_path,
            Mount(scope="user:1/layout:erp", mount_path="fixed", edit_only=True),
        )
        await memory.store.write("user:1/layout:erp", "notes", "Template body.")
        fake = _scripted(FakeTurn(content=_batch()))
        await run_reflection(
            memory_config=memory,
            turns=[_turn(1, "hello", "hi")],
            acquire=lambda _: fake,
            model=Model.FAKE,
            actor="t1",
        )
        text = str(fake.calls[0].messages[1].content)
        assert "## /fixed (edit-only — update existing documents" in text
        assert "Template body." in text  # the documents stay editable evidence

    async def test_edit_only_create_skipped_edit_lands(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        memory = _memory(
            tmp_path,
            Mount(scope="user:1/layout:erp", mount_path="fixed", edit_only=True),
        )
        await memory.store.write("user:1/layout:erp", "notes", "old body")
        bad = CreateOp(command="create", path="/fixed/extra", content="nope")
        edit = ReplaceOp(
            command="str_replace", path="/fixed/notes", old_str="old", new_str="new"
        )
        fake = _scripted(FakeTurn(content=_batch(bad, edit)))
        with caplog.at_level(logging.WARNING):
            result = await run_reflection(
                memory_config=memory,
                turns=[_turn(1, "hello", "hi")],
                acquire=lambda _: fake,
                model=Model.FAKE,
                actor="t1",
            )
        assert [w.path for w in result.writes] == ["/fixed/notes"]
        assert "memory_edit_only_mount" in caplog.text
        assert await memory.store.read("user:1/layout:erp", "extra") is None
        edited = await memory.store.read("user:1/layout:erp", "notes")
        assert edited is not None and edited.content == "new body"

    async def test_no_turns_makes_no_call(self, tmp_path: Path) -> None:
        fake = _scripted()
        result = await run_reflection(
            memory_config=_memory(tmp_path),
            turns=[],
            acquire=lambda _: fake,
            model=Model.FAKE,
            actor="t1",
        )
        assert result == ReflectionResult()
        assert fake.calls == []


@pytest.mark.unit
class TestConversationReflect:
    async def test_explicit_reflect_writes_and_clears_pending(
        self, store: FileStore
    ) -> None:
        fake = _scripted(
            FakeTurn(content="Noted."),
            FakeTurn(content=_batch(_create_op()), usage=_USAGE),
        )
        convo = Conversation(
            _config(fake),
            store=store,
            conversation_id="t1",
            memory_scope="user:1",
        )
        await convo.send("I only drink espresso.")
        result = await convo.reflect()
        assert [w.path for w in result.writes] == ["/memories/preferences"]
        assert result.usage == _USAGE
        versions = await store.versions("user:1", "preferences")
        assert versions[0].actor == "t1"
        # Pending cleared: a second reflect makes no further model call.
        assert await convo.reflect() == ReflectionResult()
        assert len(fake.calls) == 2

    async def test_reflect_without_memory_is_a_noop(self, store: FileStore) -> None:
        fake = _scripted(FakeTurn(content="Noted."))
        convo = Conversation(_config(fake), store=store, conversation_id="t1")
        await convo.send("hello")
        assert await convo.reflect() == ReflectionResult()
        assert len(fake.calls) == 1

    async def test_reflect_ignores_enabled(self, store: FileStore) -> None:
        fake = _scripted(
            FakeTurn(content="Noted."),
            FakeTurn(content=_batch(_create_op())),
        )
        convo = Conversation(
            _config(fake),
            store=store,
            conversation_id="t1",
            memory_scope="user:1",
            reflection=ReflectionConfig(enabled=False),
        )
        await convo.send("I only drink espresso.")
        result = await convo.reflect()
        assert result.writes != ()

    async def test_failed_call_keeps_pending_for_retry(self, store: FileStore) -> None:
        fake = _scripted(
            FakeTurn(content="Noted."),
            FakeTurn(content="not json"),
            FakeTurn(content=_batch(_create_op())),
        )
        convo = Conversation(
            _config(fake),
            store=store,
            conversation_id="t1",
            memory_scope="user:1",
        )
        await convo.send("I only drink espresso.")
        degraded = await convo.reflect()
        assert degraded.writes == () and degraded.degraded is not None
        result = await convo.reflect()  # the retry distills the same turns
        assert [w.path for w in result.writes] == ["/memories/preferences"]

    async def test_a_fresh_document_survives_a_boundary_delete(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        store = FileStore(tmp_path / "live")  # the system clock: written *now*
        await store.write("user:1", "today", "written moments ago")
        fake = _scripted(
            FakeTurn(content="Noted."),
            FakeTurn(
                content=_batch(DeleteOp(command="delete", path="/memories/today"))
            ),
        )
        convo = Conversation(
            _config(fake), store=store, conversation_id="t1", memory_scope="user:1"
        )
        await convo.send("forget that")
        with caplog.at_level(logging.WARNING):
            result = await convo.reflect()
        assert result.writes == () and result.degraded is None
        assert "inside the age floor" in caplog.text
        assert await store.read("user:1", "today") is not None

    async def test_a_misconfigured_reflection_model_raises(
        self, store: FileStore
    ) -> None:
        # The reflection model has its own lease (a factory sees the
        # provider); its ConfigurationError reaches the explicit caller
        # instead of degrading silently.
        fake = _scripted(FakeTurn(content="Noted."))
        other = next(m for m in Model if m.provider is not Provider.FAKE)

        def factory(provider: Provider) -> BaseLLMClient:
            if provider is not Provider.FAKE:
                raise ConfigurationError("no key for the reflection model")
            return fake

        config = AgentConfig(
            system_prompt=_SYSTEM,
            model=Model.FAKE,
            enable_todo=False,
            client_factory=factory,
        )
        convo = Conversation(
            config,
            store=store,
            conversation_id="t1",
            memory_scope="user:1",
            reflection=ReflectionConfig(model=other),
        )
        await convo.send("I only drink espresso.")
        with pytest.raises(ConfigurationError, match="reflection model"):
            await convo.reflect()
        assert await convo.aclose() is None  # a close always closes
        assert fake.closed is True


@pytest.mark.unit
class TestAcloseRider:
    async def test_aclose_reflects_then_closes(self, store: FileStore) -> None:
        fake = _scripted(
            FakeTurn(content="Noted."),
            FakeTurn(content=_batch(_create_op()), usage=_USAGE),
        )
        convo = Conversation(
            _config(fake),
            store=store,
            conversation_id="t1",
            memory_scope="user:1",
        )
        await convo.send("I only drink espresso.")
        result = await convo.aclose()
        assert result is not None
        assert [w.path for w in result.writes] == ["/memories/preferences"]
        assert fake.closed is True
        # Idempotent, and the pending turns were consumed.
        assert await convo.aclose() is None

    async def test_sendless_close_makes_no_call(self, store: FileStore) -> None:
        fake = _scripted()
        convo = Conversation(
            _config(fake),
            store=store,
            conversation_id="t1",
            memory_scope="user:1",
        )
        assert await convo.aclose() is None
        assert fake.calls == []

    async def test_disabled_rider_skips_reflection(self, store: FileStore) -> None:
        fake = _scripted(FakeTurn(content="Noted."))
        convo = Conversation(
            _config(fake),
            store=store,
            conversation_id="t1",
            memory_scope="user:1",
            reflection=ReflectionConfig(enabled=False),
        )
        await convo.send("I only drink espresso.")
        assert await convo.aclose() is None
        assert len(fake.calls) == 1
        assert fake.closed is True

    async def test_memoryless_close_returns_none(self, store: FileStore) -> None:
        fake = _scripted(FakeTurn(content="Noted."))
        convo = Conversation(_config(fake), store=store, conversation_id="t1")
        await convo.send("hello")
        assert await convo.aclose() is None
        assert len(fake.calls) == 1

    async def test_held_lock_skips_reflection_and_still_closes(
        self, store: FileStore, caplog: pytest.LogCaptureFixture
    ) -> None:
        fake = _scripted(FakeTurn(content="one"), FakeTurn(content="two"))
        convo = Conversation(
            _config(fake),
            store=store,
            conversation_id="t1",
            memory_scope="user:1",
        )
        await convo.send("first")
        events = await convo.send("second", stream=True)
        await anext(events)  # the abandoned stream holds the send lock
        with caplog.at_level(logging.WARNING):
            result = await convo.aclose()
        assert result is None
        assert "skipping reflection" in caplog.text
        assert fake.closed is True

    async def test_degraded_close_never_raises(
        self, store: FileStore, caplog: pytest.LogCaptureFixture
    ) -> None:
        fake = _scripted(FakeTurn(content="Noted."), FakeTurn(content="not json"))
        convo = Conversation(
            _config(fake),
            store=store,
            conversation_id="t1",
            memory_scope="user:1",
        )
        await convo.send("I only drink espresso.")
        with caplog.at_level(logging.WARNING):
            result = await convo.aclose()
        assert result is not None and result.writes == ()
        assert result.degraded is not None  # carried, never swallowed
        assert fake.closed is True


@pytest.mark.unit
class TestReflectionAcrossSessions:
    async def test_close_writes_memory_next_session_reads_it(
        self, tmp_path: Path
    ) -> None:
        """The NR done-when: a Conversation that never explicitly wrote
        memory ends its session and the store holds the right facts."""
        root = tmp_path / "data"

        # Session 1 — the model never calls the memory tool; the facts
        # live only in the transcript until the close distills them.
        fake_one = _scripted(
            FakeTurn(content="Espresso it is."),
            FakeTurn(content="Postgres 16, noted."),
            FakeTurn(content=_batch(_create_op()), usage=_USAGE),
        )
        convo_one = Conversation(
            _config(fake_one),
            store=FileStore(root),
            conversation_id="thread-829",
            memory_scope="user:1",
        )
        await convo_one.send("I only drink espresso.")
        await convo_one.send("This project runs on Postgres 16.")
        result = await convo_one.aclose()
        assert result is not None and result.writes != ()

        store_check = FileStore(root)
        document = await store_check.read("user:1", "preferences")
        assert document is not None and document.content == "Prefers espresso."
        versions = await store_check.versions("user:1", "preferences")
        assert versions[0].actor == "thread-829"

        # Session 2 — a fresh store and conversation: the frozen index
        # lists the reflected document and the agent reads it back.
        fake_two = _scripted(
            FakeTurn(
                tool_calls=(
                    ToolCall(
                        id=ToolCallId("c1"),
                        name=ToolName("memory"),
                        arguments={"command": "view", "path": "/memories/preferences"},
                    ),
                )
            ),
            FakeTurn(content="You drink espresso."),
        )
        convo_two = Conversation(
            _config(fake_two),
            store=FileStore(root),
            conversation_id="thread-829",
            memory_scope="user:1",
        )
        response = await convo_two.send("Where did we leave off?")
        assert "/memories/preferences" in str(fake_two.calls[0].messages[0].content)
        assert response.tool_results[0].success
        assert "Prefers espresso." in str(response.tool_results[0].data)
