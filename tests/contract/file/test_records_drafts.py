"""The records drafts command previews and discards stale items across pages."""

import json
import os
import subprocess
from pathlib import Path

from tests.contract.file.test_records import canonical, record, record_line
from tests.contract.support import Binary


def old_state(directory: Path, name: str) -> None:
    """A torn state counts from its file time and its other files."""
    directory.mkdir(parents=True)
    state = directory / name
    state.write_bytes(b"{")
    os.utime(state, (946684800, 946684800))


def test_records_drafts_streams_every_page_and_discards_only_stale_items(
    binary: Binary,
) -> None:
    root = binary.directory / "records store"
    root.mkdir()
    args = ["--root", str(root)]
    assert binary.run("records", "enable", *args).returncode == 0

    # Every restore is the only evidence of its conversation. Reverse directory
    # creation order and go one row past the default fifty-conversation page.
    expected = []
    for number in reversed(range(51)):
        conversation = f"c-{number:02}"
        restore = root / ".records" / "restore" / conversation / "backup"
        old_state(restore, "state")
        expected.append(
            {
                "conversation_id": conversation,
                "drafts": 0,
                "restores": 1,
                "freed_bytes": 1038,  # bookkeeping = 6 + 2*4 + 1024
            }
        )
    expected.reverse()

    drafts = root / "conversations" / "c-00" / ".drafts"
    old_state(drafts, "old.state")
    text = record_line(record(0, 0, "user", "prompt", "hé"))
    (drafts / "old.jsonl").write_text(text, encoding="utf-8")
    (drafts / "fresh.state").write_bytes(b"{")
    expected[0].update(drafts=1, freed_bytes=2340)  # +1035 bookkeeping, +267 record
    output = "".join(canonical(row) + "\n" for row in expected)

    preview = binary.run("records", "drafts", *args)
    assert preview.returncode == 0, preview.stderr
    assert preview.stdout == output
    assert (drafts / "old.jsonl").read_text(encoding="utf-8") == text

    discarded = binary.run("records", "drafts", "--discard", "--json", *args)
    assert discarded.returncode == 0, discarded.stderr
    assert discarded.stdout == output
    assert not (drafts / "old.state").exists()
    assert not (drafts / "old.jsonl").exists()
    assert (drafts / "fresh.state").read_bytes() == b"{"
    assert not list((root / ".records" / "restore").glob("*/*"))

    again = binary.run("records", "drafts", "--json", *args)
    assert again.returncode == 0, again.stderr
    assert again.stdout == ""


def test_records_drafts_refuses_a_missing_explicit_store_without_creating_it(
    binary: Binary,
) -> None:
    missing = binary.directory / "absent"
    refused = binary.run(
        "records", "drafts", "--discard", "--root", str(missing), "--json"
    )
    assert refused.returncode == 1, refused.stderr
    assert f"no store at {missing}" in json.loads(refused.stdout)["error"]
    assert not missing.exists()


def test_only_an_enabled_store_reads_the_sweep_age(binary: Binary) -> None:
    root = binary.directory / "configured"
    root.mkdir()
    args = ["--root", str(root)]
    for enabled in (False, True):
        if enabled:
            assert binary.run("records", "enable", *args).returncode == 0
        for flags in ([], ["--discard"]):
            result = subprocess.run(
                [binary.executable, "records", "drafts", "--json", *args, *flags],
                env=binary.environment() | {"NEOSIAN_DRAFT_SECONDS": "59"},
                cwd=binary.directory,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            expected = (
                "NEOSIAN_DRAFT_SECONDS must be an integer in 60..2592000"
                if enabled
                else "records are not enabled on this store"
            )
            assert result.returncode == 1
            assert expected in json.loads(result.stdout)["error"]
