"""Operator ledger and archive reads continue past the wire's page bound."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from neosian import FileStore
from neosian.memory import MemoryRedaction, MemoryVersion, ScopeArchive
from tests.differential.harness import Case, run_case, shape_of_output
from tests.differential.operator_seed import STAMP
from tests.differential.remote_cases import remote_case, serving
from tests.differential.seed import Seeded
from tests.differential.test_remote import _space


async def test_ledgers_versions_and_archives_cross_the_500_row_boundary(
    seeded: Seeded, python_door: Path, rust_door: Path, tmp_path: Path
) -> None:
    outputs: list[Any] = []
    for server in (python_door, rust_door):
        for client in (python_door, rust_door):
            space = _space(seeded, tmp_path / str(len(outputs)))
            store = FileStore(space.home)
            await store.restore_scope(
                ScopeArchive(
                    "user:paged",
                    (),
                    tuple(
                        MemoryVersion(
                            "gone", n, "deleted", "", None, STAMP, redacted=True
                        )
                        for n in range(1, 503)
                    ),
                    tuple(
                        MemoryRedaction(None, "opaque", STAMP, n) for n in range(1, 503)
                    ),
                )
            )
            cases = (
                Case(
                    "paged audit",
                    ("audit", "--scope", "user:paged", "--root", "{home}", "--json"),
                ),
                Case(
                    "paged versions",
                    (
                        "memory",
                        "versions",
                        "/memories/gone",
                        "--limit",
                        "501",
                        "--scope",
                        "user:paged",
                        "--root",
                        "{home}",
                        "--json",
                    ),
                ),
                Case(
                    "paged export",
                    (
                        "export",
                        "{out}",
                        "--scope",
                        "user:paged",
                        "--root",
                        "{home}",
                        "--json",
                    ),
                ),
            )
            answers = []
            with serving(server, space) as url:
                for case in cases:
                    remote = remote_case(case, url)
                    result = run_case(client, remote, space)
                    assert result.code == 0, result.stderr
                    answers.append(
                        shape_of_output(remote, result, space, keep=seeded.ids)
                    )
                    payload = json.loads(result.stdout)
                    if case.verb == "audit":
                        assert len(payload["entries"]) == 1004
                    elif case.verb == "memory versions":
                        assert len(payload["versions"]) == 501
            archive = FileStore(space.out)
            assert len(await archive.history("user:paged")) == 502
            assert len(await archive.redactions("user:paged")) == 502
            outputs.append(answers)
    assert outputs == [outputs[0]] * 4
