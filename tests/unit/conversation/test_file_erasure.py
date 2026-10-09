"""FileStore's eraser — the substrate specifics (N8, DESIGN §38).

Cross-implementation behaviour lives in the kit's `ErasureContract`
(test_file_contract.py); this file pins what only the file substrate
promises: the row on disk, the trail file, the rewrite's privacy, and
that a log the store cannot read is never rewritten.
"""

import json
import sys
from pathlib import Path

import pytest

from neosian._foundation.conversation.types import ConversationProjection
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.memory.file import FileStore
from neosian._foundation.shared.exceptions import ConversationFormatUnsupportedError

pytestmark = pytest.mark.asyncio

_EXCHANGE = (
    Message(role=Role.USER, content="the password is hunter2"),
    Message(role=Role.ASSISTANT, content="noted"),
)


def _dir(store: FileStore, conversation_id: str) -> Path:
    return store._root / "conversations" / conversation_id  # noqa: SLF001


def _mode(path: Path) -> int:
    if sys.platform == "win32":
        pytest.skip("POSIX permission bits")
    return path.stat().st_mode & 0o777


@pytest.mark.unit
class TestRowShape:
    async def test_a_redacted_row_is_the_skeleton_with_the_flag(
        self, store: FileStore
    ) -> None:
        await store.append_turn("c1", _EXCHANGE, actor="w1")
        await store.append_turn("c1", _EXCHANGE)
        await store.redact_turns("c1", turns=(1,))
        raw = (_dir(store, "c1") / "turns.jsonl").read_text(encoding="utf-8")
        assert "hunter2" in raw  # turn 2 stands
        first, second = (json.loads(line) for line in raw.splitlines())
        assert first == {
            "neosian_format": 1,
            "turn": 1,
            "created_at": first["created_at"],
            "messages": [],
            "actor": "w1",
            "redacted": True,
        }
        assert "redacted" not in second  # an unredacted row is unchanged
        assert "hunter2" not in json.dumps(first)

    async def test_an_unredacted_row_with_no_messages_is_still_refused(
        self, store: FileStore
    ) -> None:
        file = _dir(store, "c1") / "turns.jsonl"
        file.parent.mkdir(parents=True)
        file.write_text(
            '{"neosian_format":1,"turn":1,"created_at":"2026-01-01T00:00:00Z",'
            '"messages":[]}\n',
            encoding="utf-8",
        )
        with pytest.raises(ConversationFormatUnsupportedError):
            await store.read_turns("c1")

    async def test_a_redacted_row_with_messages_is_refused(
        self, store: FileStore
    ) -> None:
        file = _dir(store, "c1") / "turns.jsonl"
        file.parent.mkdir(parents=True)
        file.write_text(
            '{"neosian_format":1,"turn":1,"created_at":"2026-01-01T00:00:00Z",'
            '"messages":[{"role":"user","content":"x"}],"redacted":true}\n',
            encoding="utf-8",
        )
        with pytest.raises(ConversationFormatUnsupportedError):
            await store.read_turns("c1")


@pytest.mark.unit
class TestTheTrail:
    async def test_the_trail_lands_beside_the_log_one_act_per_line(
        self, store: FileStore
    ) -> None:
        await store.append_turn("c1", _EXCHANGE)
        await store.append_turn("c1", _EXCHANGE)
        await store.redact_turns("c1", turns=(2,), actor="ops")
        await store.redact_turns("c1", actor="ops")
        trail = _dir(store, "c1") / "redactions.jsonl"
        rows = [json.loads(line) for line in trail.read_text("utf-8").splitlines()]
        assert [row["turns"] for row in rows] == [[2], [1, 2]]
        assert rows[0]["actor"] == "ops"
        assert rows[0]["neosian_format"] == 1
        assert rows[0]["created_at"].endswith("Z")
        assert _mode(trail) == 0o600

    async def test_a_no_op_leaves_no_trail(self, store: FileStore) -> None:
        await store.append_turn("c1", _EXCHANGE)
        assert await store.redact_turns("c1", turns=(5,)) == 0
        assert not (_dir(store, "c1") / "redactions.jsonl").exists()
        assert await store.redact_turns("never") == 0
        assert not _dir(store, "never").exists()

    async def test_the_whole_store_read_skips_foreign_directories(
        self, store: FileStore
    ) -> None:
        await store.append_turn("c1", _EXCHANGE)
        await store.redact_turns("c1")
        foreign = store._root / "conversations" / "a b"  # noqa: SLF001
        foreign.mkdir(parents=True)
        (foreign / "redactions.jsonl").write_text("{broken\n", encoding="utf-8")
        acts = await store.turn_redactions()
        assert [act.conversation_id for act in acts] == ["c1"]

    async def test_a_malformed_trail_line_raises(self, store: FileStore) -> None:
        await store.append_turn("c1", _EXCHANGE)
        await store.redact_turns("c1")
        trail = _dir(store, "c1") / "redactions.jsonl"
        with trail.open("a", encoding="utf-8") as handle:
            handle.write('{"neosian_format":1,"created_at":"x","turns":[1]}\n')
        with pytest.raises(ConversationFormatUnsupportedError):
            await store.turn_redactions(conversations=["c1"])


@pytest.mark.unit
class TestTheRewrite:
    async def test_the_rewrite_stays_private(self, store: FileStore) -> None:
        await store.append_turn("c1", _EXCHANGE)
        await store.append_projections(
            "c1", (ConversationProjection(turn=1, kind="log", text="USER: hunter2"),)
        )
        await store.redact_turns("c1")
        for name in ("turns.jsonl", "projections.jsonl"):
            assert _mode(_dir(store, "c1") / name) == 0o600
        projections = (_dir(store, "c1") / "projections.jsonl").read_text("utf-8")
        assert "hunter2" not in projections
        assert json.loads(projections)["text"] == ""

    async def test_a_log_the_store_cannot_read_is_never_rewritten(
        self, store: FileStore
    ) -> None:
        await store.append_turn("c1", _EXCHANGE)
        file = _dir(store, "c1") / "turns.jsonl"
        with file.open("a", encoding="utf-8", newline="") as handle:
            handle.write("{broken\n")
        before = file.read_text(encoding="utf-8")
        with pytest.raises(ConversationFormatUnsupportedError):
            await store.redact_turns("c1")
        assert file.read_text(encoding="utf-8") == before
        assert not (_dir(store, "c1") / "redactions.jsonl").exists()

    async def test_an_untouched_projection_log_is_not_rewritten(
        self, store: FileStore
    ) -> None:
        await store.append_turn("c1", _EXCHANGE)
        await store.append_turn("c1", _EXCHANGE)
        await store.append_projections(
            "c1", (ConversationProjection(turn=2, kind="log", text="two"),)
        )
        file = _dir(store, "c1") / "projections.jsonl"
        stamp = file.stat().st_mtime_ns
        await store.redact_turns("c1", turns=(1,))
        assert file.stat().st_mtime_ns == stamp


@pytest.mark.unit
class TestMobility:
    async def test_the_skeleton_and_the_trail_survive_a_transfer(
        self, store: FileStore, tmp_path: Path
    ) -> None:
        from neosian._foundation.memory.transfer import transfer

        await store.append_turn("c1", _EXCHANGE, actor="w1")
        await store.append_turn("c1", _EXCHANGE)
        await store.append_projections(
            "c1", (ConversationProjection(turn=1, kind="log", text="hunter2"),)
        )
        await store.redact_turns("c1", turns=(1,), actor="ops")
        target = FileStore(tmp_path / "target")
        report = await transfer(store, target, conversations=["c1"])
        assert (report.units[0].turns, report.units[0].redactions) == (2, 1)
        assert await target.read_turns("c1") == await store.read_turns("c1")
        assert await target.read_projections("c1") == await store.read_projections("c1")
        assert await target.turn_redactions() == await store.turn_redactions()
        for name in ("turns.jsonl", "projections.jsonl", "redactions.jsonl"):
            text = (tmp_path / "target" / "conversations" / "c1" / name).read_text()
            assert ("hunter2" in text) == (name == "turns.jsonl")  # turn 2 stands
