"""`neosian records drafts --url` (the T1e2c slice of the T1e2 plan, DESIGN
§40.54 item 13): the sweep of a FileStore served by `neosian serve`, read
a page of fifty conversations at a time through `store/sweep_records`,
prints the JSON Lines `--root` prints, previews by default and removes
with `--discard`. The route is the operator's: a scoped token is refused.
Through a forwarding stub, a later page's failure is reported after the
rows already printed, and a page that names the cursor it was sent stops
the loop."""

from __future__ import annotations

import json
import subprocess

from tests.contract.file.test_records_drafts import plant
from tests.contract.landing.stub import Rule, forwarding
from tests.contract.records.searching import SWEEP, WHOLE_STORE, cursor
from tests.contract.support import ADMIN, LIMITED, Binary, serve

UNAVAILABLE = {"error": {"code": "unavailable", "message": "try later", "details": {}}}


def planted(binary: Binary) -> str:
    """The served home holding the planted stale items; their JSON Lines."""
    binary.home.mkdir(exist_ok=True)
    return plant(binary, binary.home)[0]


def drafts(
    binary: Binary, url: str, *args: str, token: str = ADMIN
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary.executable), "records", "drafts", "--url", url, *args],
        env=binary.environment() | {"NEOSIAN_CLIENT_TOKEN": token},
        cwd=binary.directory,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def test_a_served_store_is_swept_with_the_same_pages_as_its_root(
    binary: Binary,
) -> None:
    binary.home.mkdir(exist_ok=True)
    output, stale, text = plant(binary, binary.home)
    direct = binary.run("records", "drafts", "--root", str(binary.home))
    assert (direct.returncode, direct.stdout) == (0, output), direct.stderr
    with serve(binary) as server:
        preview = drafts(binary, server.url)
        assert preview.returncode == 0, preview.stderr
        assert preview.stdout == output
        assert (stale / "old.jsonl").read_text(encoding="utf-8") == text
        refused = drafts(binary, server.url, "--discard", "--json", token=LIMITED)
        assert (refused.returncode, json.loads(refused.stdout)) == (
            1,
            {
                "error": f"refused with 403 (forbidden): {WHOLE_STORE}",
                "hint": None,
            },
        )
        assert (stale / "old.jsonl").exists()
        discarded = drafts(binary, server.url, "--discard", "--json")
        assert discarded.returncode == 0, discarded.stderr
        assert discarded.stdout == output
        assert not (stale / "old.jsonl").exists()
        assert (stale / "fresh.state").read_bytes() == b"{"
        assert not list((binary.home / ".records" / "restore").glob("*/*"))
        again = drafts(binary, server.url, "--json")
        assert (again.returncode, again.stdout) == (0, "")


def test_a_later_page_failure_is_reported_after_the_rows_printed(
    binary: Binary,
) -> None:
    output = planted(binary)
    first = "".join(output.splitlines(keepends=True)[:50])
    with serve(binary) as server, forwarding(server.url) as stub:
        stub.add(
            Rule(
                when=lambda exchange: exchange.route == SWEEP and exchange.nth == 2,
                kind="answer",
                status=503,
                body=UNAVAILABLE,
            )
        )
        failed = drafts(binary, stub.url, "--discard")
        assert failed.returncode == 1
        assert failed.stdout == first
        assert failed.stderr == "error: refused with 503 (unavailable): try later\n"
        # The first page was removed; the row of the failed page was not.
        rest = drafts(binary, server.url)
        assert rest.stdout == output.splitlines(keepends=True)[50]


def test_a_page_that_names_the_cursor_it_was_sent_stops_the_loop(
    binary: Binary,
) -> None:
    output = planted(binary)
    first = "".join(output.splitlines(keepends=True)[:50])
    sent = cursor("sweep-v1", "c-49")
    with serve(binary) as server, forwarding(server.url) as stub:
        stub.add(
            Rule(
                when=lambda exchange: exchange.route == SWEEP and exchange.nth == 2,
                kind="rewrite",
                rewrite=lambda answer: answer | {"next_cursor": sent},
            )
        )
        stalled = drafts(binary, stub.url, "--json")
        assert stalled.returncode == 1
        asked = [exchange.body for exchange in stub.log if exchange.route == SWEEP]
        assert asked == [{"limit": 50}, {"limit": 50, "cursor": sent}]
        lines = stalled.stdout.splitlines(keepends=True)
        assert "".join(lines[:50]) == first
        assert json.loads(lines[50])["error"] == (
            "page 2 of the sweep named the cursor it was sent; nothing more was asked"
        )
        assert len(lines) == 51
        # A preview removes nothing, however it ends.
        assert drafts(binary, server.url).stdout == output
