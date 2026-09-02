"""The per-session spool — where an open span waits (DESIGN §20.9).

A hook fires once per event in a fresh process, so the prompt, the tool
rounds and the final text arrive as separate calls; the spool holds the
reduced records between them — one JSONL file per session, private
modes (ledger #126). It is never the store: the record lands at Stop,
whole, and the file goes. A failed landing keeps it, so the next Stop
carries the whole span — nothing is lost, only delayed.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from neosian._foundation.shared.fileio import append_line, private_mkdir

if TYPE_CHECKING:
    from pathlib import Path

    from neosian._foundation.record.span import Record


class Spool:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def file(self, session_id: str) -> Path:
        # A session id is one flat segment of [A-Za-z0-9_.-] (§9.4), so it
        # is a safe file name as is.
        return self._directory / f"{session_id}.jsonl"

    def append(self, session_id: str, record: Record) -> None:
        private_mkdir(self._directory)
        append_line(
            self.file(session_id), json.dumps(record, ensure_ascii=False) + "\n"
        )

    def read(self, session_id: str) -> list[Record]:
        file = self.file(session_id)
        if not file.is_file():
            return []
        records: list[Record] = []
        for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row: Any = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"spool {file} line {number} is not JSON: {exc}"
                ) from None
            if not isinstance(row, dict):
                raise ValueError(f"spool {file} line {number} is not an object")
            records.append(row)
        return records

    def clear(self, session_id: str) -> None:
        self.file(session_id).unlink(missing_ok=True)
