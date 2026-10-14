"""The spool keeps one record per newline (DESIGN §20.9): a text's own
line separators are written raw and never split a record."""

from __future__ import annotations

from pathlib import Path

from neosian._foundation.record.spool import Spool


def test_a_records_own_line_separators_never_split_it(tmp_path: Path) -> None:
    spool = Spool(tmp_path / "spool")
    spool.append("s", {"kind": "prompt", "text": "one two\u0085three"})
    spool.append("s", {"kind": "stop", "text": "done"})
    assert spool.read("s") == [
        {"kind": "prompt", "text": "one two\u0085three"},
        {"kind": "stop", "text": "done"},
    ]
    spool.clear("s")
    assert spool.read("s") == []
