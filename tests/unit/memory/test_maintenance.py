"""Memory maintenance — the gardener (DESIGN §16). Zero keys.

The deterministic stage: byte-identical duplicate merge (keep the
oldest) and empty-document prune, gated by the age floor and the
redacted skip (ledger #91/#92). The model stage: one scripted
structured-output call whose ops execute through the shared dispatcher
with protection enforced in code, degrade-only. The NG done-when's
first half: a deliberately polluted store, measurably improved by one
pass, keylessly scripted.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from neosian import Model
from neosian._foundation.llm.base import Usage
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.maintenance import (
    MaintainCreateOp,
    MaintainDeleteOp,
    MaintainOp,
    MaintainRenameOp,
    MaintainReplaceOp,
    MaintenanceBatch,
    run_maintenance,
)
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.shared.exceptions import ConfigurationError

_USAGE = Usage(input_tokens=50, output_tokens=5)
_OLD = datetime(2026, 8, 1, tzinfo=UTC)
_NOW = datetime(2026, 8, 22, tzinfo=UTC)


class _Clock:
    """A settable clock: backdate writes, then garden from `_NOW`."""

    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


def _memory(tmp_path: Path, clock: _Clock) -> MemoryConfig:
    return MemoryConfig(
        store=FileStore(tmp_path / "store", clock=clock),
        mounts=(
            Mount(scope="user:1", mount_path="user", description="the user"),
            Mount(scope="user:1/proj:x", mount_path="project"),
            Mount(scope="tenant:kb", mount_path="kb", read_only=True),
        ),
    )


def _batch(*ops: MaintainOp) -> str:
    return MaintenanceBatch(ops=list(ops)).model_dump_json()


def _scripted(*turns: FakeTurn) -> FakeClient:
    return FakeClient(FakeScript(turns=turns))


async def _paths(config: MemoryConfig, scope: str) -> list[str]:
    return [e.path for e in await config.store.list_documents(scope)]


class TestValidation:
    async def test_acquire_and_model_come_together(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path, _Clock(_NOW))
        fake = _scripted(FakeTurn(content=_batch()))
        with pytest.raises(ConfigurationError):
            await run_maintenance(memory, acquire=lambda _: fake)
        with pytest.raises(ConfigurationError):
            await run_maintenance(memory, model=Model.FAKE)

    async def test_negative_min_age_is_refused(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path, _Clock(_NOW))
        with pytest.raises(ConfigurationError):
            await run_maintenance(memory, min_age=timedelta(seconds=-1))


class TestDeterministicStage:
    async def test_byte_identical_duplicates_merge_keeping_the_oldest(
        self, tmp_path: Path
    ) -> None:
        clock = _Clock(_OLD)
        memory = _memory(tmp_path, clock)
        # zz-first is created first: created_at, not path order, picks the keeper.
        await memory.store.write("user:1", "zz-first", "Prefers dark mode")
        clock.at = _OLD + timedelta(hours=1)
        await memory.store.write("user:1", "aa-later", "Prefers dark mode")
        clock.at = _NOW
        result = await run_maintenance(memory, clock=clock, actor="gardener")
        assert [(w.command, w.path, w.version) for w in result.writes] == [
            ("delete", "/user/aa-later", None)
        ]
        assert result.usage is None and result.model is None
        assert await _paths(memory, "user:1") == ["zz-first"]

    async def test_created_at_tie_keeps_the_lexicographically_first(
        self, tmp_path: Path
    ) -> None:
        clock = _Clock(_OLD)
        memory = _memory(tmp_path, clock)
        await memory.store.write("user:1", "bb", "same fact")
        await memory.store.write("user:1", "aa", "same fact")
        clock.at = _NOW
        await run_maintenance(memory, clock=clock)
        assert await _paths(memory, "user:1") == ["aa"]

    async def test_empty_documents_are_pruned(self, tmp_path: Path) -> None:
        clock = _Clock(_OLD)
        memory = _memory(tmp_path, clock)
        await memory.store.write("user:1", "empty", "")
        await memory.store.write("user:1", "blank", "  \n\t\n")
        clock.at = _NOW
        result = await run_maintenance(memory, clock=clock)
        assert sorted(w.path for w in result.writes) == ["/user/blank", "/user/empty"]
        assert await _paths(memory, "user:1") == []

    async def test_the_age_floor_protects_fresh_documents(self, tmp_path: Path) -> None:
        clock = _Clock(_OLD)
        memory = _memory(tmp_path, clock)
        await memory.store.write("user:1", "old-dup", "same")
        clock.at = _NOW  # both the fresh duplicate and a fresh empty doc
        await memory.store.write("user:1", "new-dup", "same")
        await memory.store.write("user:1", "new-empty", "")
        result = await run_maintenance(memory, clock=clock)
        assert result.writes == ()
        assert await _paths(memory, "user:1") == ["new-dup", "new-empty", "old-dup"]

    async def test_redacted_documents_are_never_pruned(self, tmp_path: Path) -> None:
        """The empty-prune-eats-redacted trap (ledger #92): a redacted
        document reads back as empty content, and must survive."""
        clock = _Clock(_OLD)
        memory = _memory(tmp_path, clock)
        await memory.store.write("user:1", "erased", "sensitive")
        await memory.store.redact("user:1", path="erased")
        clock.at = _NOW
        result = await run_maintenance(memory, clock=clock)
        assert result.writes == ()
        assert await _paths(memory, "user:1") == ["erased"]

    async def test_read_only_mounts_are_untouched(self, tmp_path: Path) -> None:
        clock = _Clock(_OLD)
        memory = _memory(tmp_path, clock)
        await memory.store.write("tenant:kb", "dup-a", "same")
        await memory.store.write("tenant:kb", "dup-b", "same")
        await memory.store.write("tenant:kb", "empty", "")
        clock.at = _NOW
        result = await run_maintenance(memory, clock=clock)
        assert result.writes == ()
        assert await _paths(memory, "tenant:kb") == ["dup-a", "dup-b", "empty"]

    async def test_version_rows_carry_the_actor(self, tmp_path: Path) -> None:
        clock = _Clock(_OLD)
        memory = _memory(tmp_path, clock)
        await memory.store.write("user:1", "a", "same")
        await memory.store.write("user:1", "b", "same")
        clock.at = _NOW
        await run_maintenance(memory, clock=clock, actor="cli:gardener")
        versions = await memory.store.versions("user:1", "b")
        assert versions[0].action == "deleted"
        assert versions[0].actor == "cli:gardener"

    async def test_a_clean_store_is_a_no_op(self, tmp_path: Path) -> None:
        clock = _Clock(_OLD)
        memory = _memory(tmp_path, clock)
        await memory.store.write("user:1", "kept", "a real fact")
        clock.at = _NOW
        first = await run_maintenance(memory, clock=clock)
        second = await run_maintenance(memory, clock=clock)
        assert first.writes == () and second.writes == ()


class TestWireSchema:
    def test_the_op_union_is_strict_compatible(self) -> None:
        """Every property required, anyOf never oneOf — the OpenAI
        strict-mode 400 the reflection engine hit externally, pinned
        keylessly for the gardener too (NR precedent)."""
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

        check(get_json_schema(MaintenanceBatch))


class TestModelStage:
    async def test_ops_land_through_the_dispatcher_with_the_actor(
        self, tmp_path: Path
    ) -> None:
        clock = _Clock(_OLD)
        memory = _memory(tmp_path, clock)
        await memory.store.write("user:1", "editor", "Uses vim")
        await memory.store.write("user:1", "editor-setup", "Switched to neovim")
        await memory.store.write("user:1/proj:x", "owner", "Ada owns deploys")
        clock.at = _NOW
        fake = _scripted(
            FakeTurn(
                content=_batch(
                    MaintainReplaceOp(
                        command="str_replace",
                        path="/user/editor",
                        old_str="Uses vim",
                        new_str="Uses neovim (switched July 2026)",
                    ),
                    MaintainDeleteOp(command="delete", path="/user/editor-setup"),
                    MaintainRenameOp(
                        command="rename",
                        old_path="/project/owner",
                        new_path="/user/owner",
                    ),
                ),
                usage=_USAGE,
            )
        )
        result = await run_maintenance(
            memory,
            acquire=lambda _: fake,
            model=Model.FAKE,
            actor="gardener",
            clock=clock,
        )
        assert [(w.command, w.path) for w in result.writes] == [
            ("str_replace", "/user/editor"),
            ("delete", "/user/editor-setup"),
            ("rename", "/user/owner"),
        ]
        assert result.usage == _USAGE and result.model is not None
        merged = await memory.store.read("user:1", "editor")
        assert merged is not None and "neovim" in merged.content
        assert await memory.store.read("user:1", "editor-setup") is None
        promoted = await memory.store.read("user:1", "owner")
        assert promoted is not None and promoted.content == "Ada owns deploys"
        assert promoted.actor == "gardener"
        assert await memory.store.read("user:1/proj:x", "owner") is None

    async def test_protection_blocks_fresh_deletes_and_redacted_ops(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        clock = _Clock(_OLD)
        memory = _memory(tmp_path, clock)
        await memory.store.write("user:1", "erased", "gone")
        await memory.store.redact("user:1", path="erased")
        clock.at = _NOW
        await memory.store.write("user:1", "fresh", "written today")
        fake = _scripted(
            FakeTurn(
                content=_batch(
                    MaintainDeleteOp(command="delete", path="/user/fresh"),
                    MaintainReplaceOp(
                        command="str_replace",
                        path="/user/erased",
                        old_str="gone",
                        new_str="rewritten",
                    ),
                    MaintainRenameOp(
                        command="rename",
                        old_path="/user/erased",
                        new_path="/user/elsewhere",
                    ),
                ),
                usage=_USAGE,
            )
        )
        with caplog.at_level(logging.WARNING):
            result = await run_maintenance(
                memory, acquire=lambda _: fake, model=Model.FAKE, clock=clock
            )
        assert result.writes == ()
        assert "age floor" in caplog.text and "redacted" in caplog.text
        assert await _paths(memory, "user:1") == ["erased", "fresh"]
        fresh = await memory.store.read("user:1", "fresh")
        assert fresh is not None and fresh.content == "written today"

    async def test_a_failed_model_call_degrades_to_the_deterministic_result(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        clock = _Clock(_OLD)
        memory = _memory(tmp_path, clock)
        await memory.store.write("user:1", "a", "same")
        await memory.store.write("user:1", "b", "same")
        clock.at = _NOW

        def refuse(_: object) -> FakeClient:
            raise RuntimeError("no client tonight")

        with caplog.at_level(logging.WARNING):
            result = await run_maintenance(
                memory, acquire=refuse, model=Model.FAKE, clock=clock
            )
        assert [(w.command, w.path) for w in result.writes] == [("delete", "/user/b")]
        assert result.usage is None and result.model is None
        assert "Maintenance failed; degrading" in caplog.text

    async def test_a_failed_op_is_skipped_while_the_rest_land(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        clock = _Clock(_OLD)
        memory = _memory(tmp_path, clock)
        await memory.store.write("user:1", "stale", "old claim")
        clock.at = _NOW
        fake = _scripted(
            FakeTurn(
                content=_batch(
                    MaintainReplaceOp(
                        command="str_replace",
                        path="/user/stale",
                        old_str="never present",
                        new_str="x",
                    ),
                    MaintainCreateOp(
                        command="create", path="/kb/nope", content="read-only"
                    ),
                    MaintainDeleteOp(command="delete", path="/user/stale"),
                ),
                usage=_USAGE,
            )
        )
        with caplog.at_level(logging.WARNING):
            result = await run_maintenance(
                memory, acquire=lambda _: fake, model=Model.FAKE, clock=clock
            )
        assert [(w.command, w.path) for w in result.writes] == [
            ("delete", "/user/stale")
        ]
        assert "str_replace" in caplog.text and "read_only" in caplog.text
        assert await _paths(memory, "user:1") == []
        assert await _paths(memory, "tenant:kb") == []


class TestEvidencePayload:
    async def test_annotations_markers_and_exclusions(self, tmp_path: Path) -> None:
        from neosian._foundation.memory.maintenance import _render_evidence

        clock = _Clock(_OLD)
        memory = _memory(tmp_path, clock)
        await memory.store.write("user:1", "old-fact", "Old but true")
        await memory.store.write("user:1", "erased", "secret")
        await memory.store.redact("user:1", path="erased")
        clock.at = _NOW
        await memory.store.write("user:1", "fresh-fact", "New today")
        await memory.store.write("tenant:kb", "ref", "reference")
        cutoff = _NOW - timedelta(days=7)
        payload = await _render_evidence(memory, cutoff)
        assert "### /user/old-fact [v1 | created 2026-08-01 | updated 2026-08-01]" in (
            payload
        )
        assert "Old but true" in payload
        assert "[fresh — protected from deletion]" in payload
        assert "### /user/erased (redacted — protected, take no action)" in payload
        assert "secret" not in payload
        assert "/kb" not in payload and "reference" not in payload


class TestDoneWhen:
    async def test_a_polluted_store_is_measurably_improved_by_one_pass(
        self, tmp_path: Path
    ) -> None:
        """The NG done-when, first half: dupes, stale, misfiled and empty
        in; one pass out — keylessly scripted (ledger #90/#91)."""
        clock = _Clock(_OLD)
        memory = _memory(tmp_path, clock)
        await memory.store.write("user:1", "preferences", "Prefers dark mode")
        await memory.store.write("user:1", "scratch", "")
        await memory.store.write("user:1", "python", "We target Python 3.11")
        await memory.store.write("user:1/proj:x", "owner", "Ada owns deploys")
        clock.at = _OLD + timedelta(days=1)  # the later duplicate
        await memory.store.write("user:1", "prefs", "Prefers dark mode")
        clock.at = _NOW
        await memory.store.write("user:1", "today", "Started using uv")
        before = len(await _paths(memory, "user:1")) + len(
            await _paths(memory, "user:1/proj:x")
        )
        from neosian import Model

        fake = _scripted(
            FakeTurn(
                content=_batch(
                    MaintainReplaceOp(
                        command="str_replace",
                        path="/user/python",
                        old_str="3.11",
                        new_str="3.12",
                    ),
                    MaintainRenameOp(
                        command="rename",
                        old_path="/project/owner",
                        new_path="/user/owner",
                    ),
                ),
                usage=_USAGE,
            )
        )
        result = await run_maintenance(
            memory,
            acquire=lambda _: fake,
            model=Model.FAKE,
            actor="gardener",
            clock=clock,
        )
        after_user = await _paths(memory, "user:1")
        after_proj = await _paths(memory, "user:1/proj:x")
        assert len(after_user) + len(after_proj) < before
        assert after_proj == []  # the misfiled fact was promoted
        assert "owner" in after_user and "preferences" in after_user
        assert "prefs" not in after_user  # the later duplicate merged away
        assert "scratch" not in after_user  # the empty doc pruned
        assert "today" in after_user  # the fresh doc survived
        corrected = await memory.store.read("user:1", "python")
        assert corrected is not None and "3.12" in corrected.content
        assert result.usage == _USAGE
        assert {w.command for w in result.writes} == {
            "delete",
            "str_replace",
            "rename",
        }
