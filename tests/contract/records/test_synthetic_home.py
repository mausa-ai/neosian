"""The synthetic home imports fully through `neosian import DIR --url` into a
FileStore that keeps records and reads back identically (the T1gb slice of
the T1f plan, DESIGN §40.54 item 16.5).

The home of `synthetic.py` is imported with the CLI into a fresh store that
keeps records, through a meter that counts what is sent, and into a twin
store without records through the legacy import. Then, for every
conversation, the paged unfiltered `read_records` answer equals the
archive's turns converted by item 3.3 and split by item 3.4, record by
record, with counts and bytes per kind; `read_turns` sends the twin's bytes
and holds the archive's turns; each restore committed the unit digest
computed from the archive; the projections and the whole erasure trail in
stored order are the archive's on both stores; every scope, document and
version reads back on both; and the archive is unchanged. The one-tenth
profile runs here by default; `NEOSIAN_SYNTHETIC_PROFILE=full` runs the
full one, and `NEOSIAN_SYNTHETIC_REPORT=FILE` appends its costs to FILE.
"""

from __future__ import annotations

import random
from collections.abc import Iterator

import pytest

from tests.contract.records.differential import (
    Home,
    check_entries_and_trails,
    check_full_view,
    check_memory,
    check_read_turns,
    check_units,
    file_trail,
    imported_home,
    split,
)
from tests.contract.records.recalling import split as reference_split
from tests.contract.records.support import enable
from tests.contract.records.synthetic import Texts
from tests.contract.support import Binary, executable, serve

pytestmark = pytest.mark.timeout(3_600)


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Home]:
    base = tmp_path_factory.mktemp("synthetic")
    kept, legacy = Binary(executable(), base / "kept"), Binary(
        executable(), base / "twin"
    )
    for binary in (kept, legacy):
        binary.directory.mkdir()
    enable(kept)
    with imported_home(
        base,
        "files",
        (kept, serve, file_trail(kept.home)),
        (legacy, serve, file_trail(legacy.home)),
    ) as imported:
        yield imported


def test_the_fast_split_is_the_reference_rule() -> None:
    texts = Texts(random.Random(7))
    samples = [
        texts.kind(kind)(150_000) for kind in ("prose", "line", "blob", "tangle")
    ]
    samples += ["", "a" * 65_534, "a" * 65_535, "\x01" * 20_000 + "b" * 70_000]
    samples += ["é" * 40_000, "x" * 61_000 + "\x1f" + "y" * 9_000, "\n" * 70_000]
    for text in samples:
        assert split(text) == reference_split(text), text[:40]


def test_the_import_reports_every_unit_and_leaves_the_archive_intact(
    home: Home,
) -> None:
    check_units(home)


def test_every_conversation_reads_back_in_the_full_view_and_its_digest(
    home: Home,
) -> None:
    check_full_view(home)


def test_read_turns_sends_the_twins_bytes_and_the_archive_turns(home: Home) -> None:
    check_read_turns(home)


def test_projections_and_the_whole_trail_match_on_both_stores(home: Home) -> None:
    check_entries_and_trails(home)


def test_memory_scopes_read_back_on_both_stores(home: Home) -> None:
    check_memory(home)
