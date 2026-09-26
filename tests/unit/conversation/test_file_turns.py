"""FileStore turn persistence — the substrate specifics (DESIGN §9.8).

Cross-implementation behavior lives in the conformance kit
(test_file_contract.py); this file pins what only the file substrate
promises: layout, row shape, last-line numbering, refusals.
"""

import json
import sys
from pathlib import Path

import pytest

from neosian._foundation.llm.base import Message, Role
from neosian._foundation.memory.file import FileStore
from neosian._foundation.shared.exceptions import (
    ConversationFormatUnsupportedError,
)

pytestmark = pytest.mark.asyncio

_EXCHANGE = (
    Message(role=Role.USER, content="hi"),
    Message(role=Role.ASSISTANT, content="hello"),
)


def _turns_file(store: FileStore, conversation_id: str) -> Path:
    return (
        store._root / "conversations" / conversation_id / "turns.jsonl"
    )  # noqa: SLF001


def _mode(path: Path) -> int:
    if sys.platform == "win32":
        pytest.skip("POSIX permission bits")
    return path.stat().st_mode & 0o777


@pytest.mark.unit
class TestLayout:
    async def test_the_turn_log_is_private(self, store: FileStore) -> None:
        # Ledger #126: turns carry the conversation verbatim; 0600/0700
        # by decision, no longer the umask default.
        await store.append_turn("c1", _EXCHANGE)
        turns_file = _turns_file(store, "c1")
        assert _mode(turns_file) == 0o600
        assert _mode(turns_file.parent) == 0o700
        assert _mode(turns_file.parent.parent) == 0o700

    async def test_turn_log_lands_under_conversations(self, store: FileStore) -> None:
        await store.append_turn("thread-1", _EXCHANGE)
        assert _turns_file(store, "thread-1").is_file()

    async def test_projections_land_beside_turns(self, store: FileStore) -> None:
        from neosian._foundation.conversation.types import ConversationProjection

        await store.append_projections(
            "thread-1", (ConversationProjection(turn=1, kind="log", text="x"),)
        )
        file = _turns_file(store, "thread-1").with_name("projections.jsonl")
        assert file.is_file()

    async def test_conversations_dir_never_collides_with_a_scope(
        self, store: FileStore
    ) -> None:
        """Scope directories are percent-encoded (`%3A` in every component),
        so the literal `conversations/` sibling is collision-free."""
        await store.write("conversations:1", "doc", "memory body")
        await store.append_turn("1", _EXCHANGE)
        document = await store.read("conversations:1", "doc")
        assert document is not None
        assert len(await store.read_turns("1")) == 1
        root = store._root  # noqa: SLF001
        assert (root / "conversations%3A1").is_dir()
        assert (root / "conversations" / "1").is_dir()


@pytest.mark.unit
class TestRowShape:
    async def test_one_compact_ascii_line_per_turn(self, store: FileStore) -> None:
        tricky = (
            Message(role=Role.USER, content="line one\nline two — café ✓"),
            Message(role=Role.ASSISTANT, content="ok\r\ndone"),
        )
        await store.append_turn("thread-1", tricky)
        await store.append_turn("thread-1", _EXCHANGE)
        raw = _turns_file(store, "thread-1").read_text(encoding="utf-8")
        lines = raw.splitlines()
        assert len(lines) == 2
        assert raw.isascii()
        first = json.loads(lines[0])
        assert first["neosian_format"] == 1
        assert first["turn"] == 1
        assert first["created_at"].endswith("Z")
        assert [m["role"] for m in first["messages"]] == ["user", "assistant"]

    async def test_unknown_row_keys_are_ignored(self, store: FileStore) -> None:
        await store.append_turn("thread-1", _EXCHANGE)
        file = _turns_file(store, "thread-1")
        row = json.loads(file.read_text(encoding="utf-8"))
        row["zzz_future_key"] = True
        row["turn"] = 2
        with file.open("a", encoding="utf-8", newline="") as handle:
            handle.write(json.dumps(row) + "\n")
        turns = await store.read_turns("thread-1")
        assert [t.turn for t in turns] == [1, 2]


@pytest.mark.unit
class TestNumbering:
    async def test_last_turn_number_reads_the_last_row_not_a_line_count(
        self, store: FileStore
    ) -> None:
        await store.append_turn("thread-1", _EXCHANGE)
        file = _turns_file(store, "thread-1")
        planted = (
            '{"neosian_format":1,"turn":7,"created_at":"2026-01-01T00:00:00Z",'
            '"messages":[{"role":"user","content":"x"}]}\n'
        )
        with file.open("a", encoding="utf-8", newline="") as handle:
            handle.write(planted)
        assert await store.last_turn_number("thread-1") == 7
        appended = await store.append_turn("thread-1", _EXCHANGE)
        assert appended.turn == 8


@pytest.mark.unit
class TestRefusals:
    async def test_malformed_line_raises_never_skips(self, store: FileStore) -> None:
        await store.append_turn("thread-1", _EXCHANGE)
        file = _turns_file(store, "thread-1")
        with file.open("a", encoding="utf-8", newline="") as handle:
            handle.write("{broken\n")
        with pytest.raises(ConversationFormatUnsupportedError) as excinfo:
            await store.read_turns("thread-1")
        assert excinfo.value.code == "agent_conversation_format_unsupported"

    async def test_naive_timestamp_is_refused_never_coerced(
        self, store: FileStore
    ) -> None:
        file = _turns_file(store, "thread-1")
        file.parent.mkdir(parents=True)
        file.write_text(
            '{"neosian_format":1,"turn":1,"created_at":"2026-01-01T00:00:00",'
            '"messages":[{"role":"user","content":"x"}]}\n',
            encoding="utf-8",
        )
        with pytest.raises(ConversationFormatUnsupportedError):
            await store.read_turns("thread-1")

    async def test_undecodable_message_is_refused(self, store: FileStore) -> None:
        file = _turns_file(store, "thread-1")
        file.parent.mkdir(parents=True)
        file.write_text(
            '{"neosian_format":1,"turn":1,"created_at":"2026-01-01T00:00:00Z",'
            '"messages":[{"role":"nope","content":"x"}]}\n',
            encoding="utf-8",
        )
        with pytest.raises(ConversationFormatUnsupportedError):
            await store.read_turns("thread-1")


@pytest.mark.unit
class TestSearchScan:
    """The file substrate's search (DESIGN §32): the raw-line prefilter
    decodes only candidates, so a malformed line lacking every term is
    invisible to a search while reads still refuse it."""

    async def test_a_non_candidate_line_is_never_decoded(
        self, store: FileStore
    ) -> None:
        await store.append_turn("c1", (Message(role=Role.USER, content="pelican"),))
        with _turns_file(store, "c1").open("a", encoding="utf-8") as handle:
            handle.write("{broken\n")
        (hit,) = await store.search_turns("pelican")
        assert hit.turn == 1
        with pytest.raises(ConversationFormatUnsupportedError):
            await store.read_turns("c1")

    async def test_a_candidate_malformed_line_raises_as_a_read_does(
        self, store: FileStore
    ) -> None:
        await store.append_turn("c1", (Message(role=Role.USER, content="pelican"),))
        with _turns_file(store, "c1").open("a", encoding="utf-8") as handle:
            handle.write("{broken pelican\n")
        with pytest.raises(ConversationFormatUnsupportedError):
            await store.search_turns("pelican")

    async def test_terms_the_file_escapes_skip_the_prefilter(
        self, store: FileStore
    ) -> None:
        content = 'say "hi" to café \\x'
        await store.append_turn("c1", (Message(role=Role.USER, content=content),))
        raw = _turns_file(store, "c1").read_text(encoding="utf-8")
        assert "caf\\u00e9" in raw  # the file holds the escape, not the letter
        for term in ('"hi"', "café", "\\x", "CAFÉ"):
            assert len(await store.search_turns(term)) == 1, term

    async def test_the_whole_store_walk_skips_foreign_directories(
        self, store: FileStore
    ) -> None:
        await store.append_turn("c1", (Message(role=Role.USER, content="osprey"),))
        foreign = store._root / "conversations" / "a b"  # noqa: SLF001
        foreign.mkdir(parents=True)
        (foreign / "turns.jsonl").write_text("{broken osprey\n", encoding="utf-8")
        found = await store.search_turns("osprey")
        assert [(t.conversation_id, t.turn) for t in found] == [("c1", 1)]

    async def test_a_missing_log_is_no_hit(self, store: FileStore) -> None:
        assert await store.search_turns("anything", conversations=["never"]) == ()
        assert await store.search_turns("anything") == ()
