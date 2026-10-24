"""Archive fidelity, all-unit preflight and whole-conversation erasure."""

from __future__ import annotations

import json
from contextlib import nullcontext
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from neosian import FileStore
from neosian._foundation.memory.transfer import archive_conversation, archive_scope
from neosian.conversation import ConversationProjection
from neosian.memory import ConversationArchive, MemoryRedaction, ScopeArchive
from tests.differential.harness import (
    Case,
    Workspace,
    run_case,
    shape_of_output,
    snapshot,
)
from tests.differential.operator_seed import OLD, SCOPE, STAMP, augment
from tests.differential.remote_cases import remote_case, serving
from tests.differential.seed import Seeded
from tests.differential.test_remote import _space


def _case(verb: str, *argv: str) -> Case:
    return Case(f"operator {verb}", (verb, *argv, "--root", "{home}", "--json"))


def _pairs(python: Path, rust: Path) -> list[tuple[Path | None, Path]]:
    return [(None, python), (None, rust)] + [
        (server, client) for server in (python, rust) for client in (python, rust)
    ]


async def _same_store(source: FileStore, target: FileStore) -> None:
    assert await source.scopes() == await target.scopes()
    assert await source.conversations() == await target.conversations()
    for scope in await source.scopes():
        assert await archive_scope(source, scope) == await archive_scope(target, scope)
    for name in await source.conversations():
        assert await archive_conversation(source, name) == await archive_conversation(
            target, name
        )


async def test_archives_round_trip_across_every_client_server_pair(
    seeded: Seeded, python_door: Path, rust_door: Path, tmp_path: Path
) -> None:
    reports: list[Any] = []
    for index, (server, client) in enumerate(_pairs(python_door, rust_door)):
        space = _space(seeded, tmp_path / str(index))
        source = await augment(space)
        with serving(server, space) if server else nullcontext(None) as url:
            case = _case("export", "{out}")
            case = remote_case(case, url) if url else case
            result = run_case(client, case, space)
            assert result.code == 0, result.stderr
            reports.append(json.loads(result.stdout)["units"])
        await _same_store(source, FileStore(space.out))
        target = replace(space, home=space.home.parent / "restored")
        target.home.mkdir()
        with serving(server, target) if server else nullcontext(None) as url:
            case = _case("import", "{out}")
            case = remote_case(case, url) if url else case
            result = run_case(client, case, target)
            assert result.code == 0, result.stderr
            assert json.loads(result.stdout)["units"] == reports[-1]
        await _same_store(source, FileStore(target.home))
    assert reports == [reports[0]] * len(reports)


@pytest.mark.parametrize("collision", ["history", "trail", "projection"])
async def test_import_preflights_every_unit_before_the_first_restore(
    collision: str, seeded: Seeded, python_door: Path, rust_door: Path, tmp_path: Path
) -> None:
    for index, (server, client) in enumerate(_pairs(python_door, rust_door)):
        space = _space(seeded, tmp_path / str(index))
        await augment(space)
        target = replace(space, home=space.home.parent / "target")
        store = FileStore(target.home)
        if collision == "history":
            await store.write(SCOPE, "deleted", "present")
            await store.delete(SCOPE, "deleted")
        elif collision == "trail":
            await store.restore_scope(
                ScopeArchive(SCOPE, (), (), (MemoryRedaction(None, None, STAMP, 1),))
            )
        else:
            await store.restore_conversation(
                ConversationArchive(
                    OLD, (), (ConversationProjection(1, "log", "occupied"),)
                )
            )
        before = snapshot(target, keep=seeded.ids)
        with serving(server, target) if server else nullcontext(None) as url:
            case = _case("import", str(space.home))
            case = remote_case(case, url) if url else case
            result = run_case(client, case, target)
            assert result.code == 1, result.stderr
            assert "target_occupied" in json.loads(result.stdout)["error"]
        assert snapshot(target, keep=seeded.ids) == before


def _invoke(
    case: Case,
    *,
    client: Path,
    space: Workspace,
    url: str | None,
    results: list[Any],
    keep: frozenset[str],
) -> dict[str, Any]:
    case = remote_case(case, url) if url else case
    result = run_case(client, case, space)
    assert result.code == 0, result.stderr
    shaped = shape_of_output(case, result, space, keep=keep)
    shaped[1][0]["client"] = None
    results.append(shaped)
    payload: dict[str, Any] = json.loads(result.stdout)
    return payload


async def test_partial_then_whole_erasure_and_prune_preserve_only_skeletons(
    seeded: Seeded, python_door: Path, rust_door: Path, tmp_path: Path
) -> None:
    observed: list[Any] = []
    for index, (server, client) in enumerate(_pairs(python_door, rust_door)):
        space = _space(seeded, tmp_path / str(index))
        store = await augment(space)
        results: list[Any] = []
        with serving(server, space) if server else nullcontext(None) as url:

            invoke = partial(
                _invoke,
                client=client,
                space=space,
                url=url,
                results=results,
                keep=seeded.ids,
            )

            invoke(_case("redact", OLD, "--turns", "1,3,1"))
            turns = await store.read_turns(OLD)
            assert [row.redacted for row in turns] == [True, False, True, False]
            assert all(not row.text for row in await store.read_projections(OLD))
            summary = await store.read(SCOPE, f"sessions/{OLD}")
            assert summary is not None and summary.content
            before = snapshot(space, keep=seeded.ids)
            plan = invoke(_case("prune", "--older-than", "1d", "--dry-run"))
            assert snapshot(space, keep=seeded.ids) == before
            assert [row["conversation_id"] for row in plan["conversations"]] == [OLD]
            assert len(plan["documents"]) == 2
            done = invoke(_case("prune", "--older-than", "1d"))
            assert done["turns"] == 4
            assert done["documents"] == plan["documents"]
            assert all(
                row.redacted and not row.messages for row in await store.read_turns(OLD)
            )
            for scope in (SCOPE, "user:other"):
                document = await store.read(scope, f"sessions/{OLD}")
                assert (
                    document is not None and document.redacted and not document.content
                )
                assert all(
                    row.redacted and not row.content
                    for row in await store.versions(scope, f"sessions/{OLD}")
                )
            again = invoke(_case("prune", "--older-than", "1d"))
            assert again["turns"] == 0 and again["documents"] == []
            assert len(await store.turn_redactions(conversations=[OLD])) == 2
            invoke(_case("audit", "--scope", SCOPE, "--conversation", OLD))
        # Local writes are stamped cli:local, remote writes client:default/cli:local.
        observed.append(results)
    assert observed[:2] == [observed[0]] * 2
    assert observed[2:] == [observed[2]] * 4
