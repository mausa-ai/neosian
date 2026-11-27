"""The expected-data reader and the differential of the synthetic home (the
T1gb slice of the T1f plan, DESIGN §40.54 items 3, 10, 12, 16.5 and 22),
written with no product code.

`synthetic.py` writes the archive. What the full view, `read_turns`, the
projections, the trail, the scopes and each committed unit digest must be
is computed here from the archive's files alone, never from the generator's
own values and never from what the binary prints. Two rules are the
product's own, and the design states each as the rule every binary uses, so
they are mirrored here as written: item 3.3's conversion of a message to
records (its pieces in order, the first record carrying the content form,
`call_id` on every `tool_result` record and on the first piece, one empty
record for a message with no piece) and item 3.4's split of a piece whose
text encodes to more than 65,536 bytes. The split is `recalling.split`'s
rule, made fast with C-speed encoding and checked against it. JSON pieces
are compact JSON with keys in the row's order, which is the codec's order
for a row in canonical form. The store's projection order (by turn, then
span, insertion breaking ties) is the one `importing.py` states.

The full view is the unfiltered, paged `read_records` answer (no `kinds`,
no `heads`, the default page). `read_turns` of a whole conversation is
compared as the bytes sent, first with the archive's turns encoded as the
wire encodes an answer (compact JSON, non-ASCII raw), then with a twin store
without records that received the same archive through the legacy import.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from tests.contract.records.importing import restore_id
from tests.contract.records.reading import PAGE_KEYS, READ
from tests.contract.records.restoring import RESTORE
from tests.contract.records.support import compact
from tests.contract.records.synthetic import write_home
from tests.contract.support import ADMIN, Binary, Server

TEXT_BYTES = 65_536
WINDOW = 4_096
PART_BYTES = 983_040
PART_ITEMS = 2_048
KINDS = {"user": "prompt", "assistant": "reply", "system": "system"}
KINDS |= {"tool": "tool_result"}
PROFILE = "NEOSIAN_SYNTHETIC_PROFILE"
REPORT = "NEOSIAN_SYNTHETIC_REPORT"


def encoded(text: str) -> int:
    """The bytes of `text` as a compact JSON string, quotes included."""
    return len(json.dumps(text, ensure_ascii=False).encode())


def split(text: str) -> list[str]:
    """Item 3.4: the longest prefix within 65,536 encoded bytes, cut after
    the last line feed in its last 4,096 UTF-8 bytes, else after the last
    white space there (Python's `isspace`), else where it stands."""
    chunks: list[str] = []
    start = 0
    while True:
        high = min(len(text) - start, TEXT_BYTES - 2)
        if encoded(text[start : start + high]) > TEXT_BYTES:
            low = 0
            while low < high:
                middle = (low + high + 1) // 2
                if encoded(text[start : start + middle]) <= TEXT_BYTES:
                    low = middle
                else:
                    high = middle - 1
        prefix = text[start : start + high]
        if start + len(prefix) < len(text):
            tail = prefix[-WINDOW:].encode()
            cut = max(0, len(tail) - WINDOW)
            while cut < len(tail) and tail[cut] & 0xC0 == 0x80:
                cut += 1
            window = len(prefix) - len(tail[cut:].decode())
            feed = prefix.rfind("\n", window)
            if feed >= 0:
                prefix = prefix[: feed + 1]
            else:
                for at in range(len(prefix) - 1, window - 1, -1):
                    if prefix[at].isspace():
                        prefix = prefix[: at + 1]
                        break
        chunks.append(prefix)
        start += len(prefix)
        if start >= len(text):
            return chunks


def pieces(
    message: dict[str, Any],
) -> tuple[str, list[tuple[str, str, dict[str, Any]]]]:
    """Item 3.3: the content form and the message's pieces in order, each
    its kind, its text and its `block`, `call`, `call_id` and `name`."""
    kind = KINDS[message["role"]]
    content = message["content"]
    made: list[tuple[str, str, dict[str, Any]]] = []
    if isinstance(content, str):
        made.append((kind, content, {}))
    elif isinstance(content, list):
        for index, block in enumerate(content):
            if block["type"] == "text":
                made.append((kind, block["text"], {"block": index}))
            else:
                made.append(("attachment", compact(block).decode(), {"block": index}))
    for index, call in enumerate(message["tool_calls"]):
        named = {"call": index, "call_id": call["id"], "name": call["name"]}
        made.append(("tool_call", compact(call["arguments"]).decode(), named))
        if call.get("extra"):
            made.append(("extra", compact(call["extra"]).decode(), {"call": index}))
    if message["reasoning"] is not None:
        made.append(("reasoning", compact(message["reasoning"]).decode(), {}))
    if message.get("extra"):
        made.append(("extra", compact(message["extra"]).decode(), {}))
    if not made or (message.get("tool_call_id") and made[0][0] == "tool_call"):
        made.insert(0, (kind, "", {}))
    form = (
        "none" if content is None else "text" if isinstance(content, str) else "blocks"
    )
    return form, made


def records_of(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """A turn's records in write shape, every key in item 3.1's order, and
    the header's `bytes`: the sum of `size` over its pieces."""
    items: list[dict[str, Any]] = []
    total = 0
    for index, message in enumerate(messages):
        form, made = pieces(message)
        result = message.get("tool_call_id") or None
        for number, (kind, text, named) in enumerate(made):
            size = len(text.encode())
            total += size
            call_id = named.get("call_id")
            if kind != "tool_call" and (kind == "tool_result" or number == 0):
                call_id = result
            for chunk, part in enumerate(split(text)):
                items.append(
                    {
                        "seq": len(items),
                        "message": index,
                        "role": message["role"],
                        "kind": kind,
                        "content": form if number == chunk == 0 else None,
                        "block": named.get("block"),
                        "call": named.get("call"),
                        "call_id": call_id,
                        "name": named.get("name"),
                        "chunk": chunk,
                        "size": size,
                        "text": part,
                    }
                )
    return items, total


@dataclass
class Expected:
    """One turn as the archive says the stores must answer it."""

    header: dict[str, Any]
    records: list[dict[str, Any]]
    turn: dict[str, Any]


def rows(path: Path) -> Iterator[dict[str, Any]]:
    if path.exists():
        with path.open(encoding="utf-8") as file:
            for line in file:
                yield json.loads(line)


def conversations(root: Path) -> list[str]:
    return sorted(p.name for p in (root / "conversations").iterdir())


def turns(root: Path, conversation: str, convert: bool = True) -> Iterator[Expected]:
    """Each turn of the archive, one at a time; without `convert` its
    header holds no `records` or `bytes` and it has no records."""
    for row in rows(root / "conversations" / conversation / "turns.jsonl"):
        made, size = records_of(row["messages"]) if convert else ([], 0)
        redacted = bool(row.get("redacted", False))
        header = {"turn": row["turn"], "created_at": row["created_at"]}
        header |= {"actor": row.get("actor"), "redacted": redacted}
        header |= {"records": len(made), "bytes": size} if convert else {}
        turn = {"conversation_id": conversation, "turn": row["turn"]}
        turn |= {"messages": row["messages"], "created_at": row["created_at"]}
        turn |= {"actor": row.get("actor"), "redacted": redacted}
        yield Expected(header, made, turn)


def projections(root: Path, conversation: str) -> list[dict[str, Any]]:
    """The store's order: by turn, then span, insertion breaking ties."""
    entries = [
        {"turn": r["turn"], "kind": r["kind"], "text": r["text"], "span": r["span"]}
        for r in rows(root / "conversations" / conversation / "projections.jsonl")
    ]
    return sorted(entries, key=lambda p: (p["turn"], p["span"]))


def acts(root: Path, conversation: str) -> list[dict[str, Any]]:
    return [
        {"conversation_id": conversation, "turns": r["turns"]}
        | {"actor": r["actor"], "created_at": r["created_at"]}
        for r in rows(root / "conversations" / conversation / "redactions.jsonl")
    ]


def scope_name(root: Path, directory: Path) -> str:
    return "/".join(p.replace("%3A", ":") for p in directory.relative_to(root).parts)


def scopes(root: Path) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Each scope's documents, by path, as its version rows: a scope is a
    directory outside `conversations` holding `documents` and `versions`."""
    found: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for versions in sorted(root.rglob("versions")):
        if (
            "conversations" in versions.relative_to(root).parts
            or not (versions.parent / "documents").is_dir()
        ):
            continue
        scope = scope_name(root, versions.parent)
        found[scope] = {}
        for journal in sorted(versions.rglob("*.jsonl")):
            path = journal.relative_to(versions).as_posix().removesuffix(".jsonl")
            history = [
                {k: v for k, v in r.items() if k != "neosian_format"}
                for r in rows(journal)
            ]
            found[scope][path] = history
    return found


def units(root: Path) -> list[dict[str, Any]]:
    """The import's units: every scope, then every conversation."""
    made = []
    for scope, documents in scopes(root).items():
        versions = sum(len(history) for history in documents.values())
        made.append(unit("scope", scope, documents=len(documents), versions=versions))
    for conversation in conversations(root):
        directory = root / "conversations" / conversation
        made.append(
            unit(
                "conversation",
                conversation,
                redactions=len(list(rows(directory / "redactions.jsonl"))),
                turns=len(list(rows(directory / "turns.jsonl"))),
                projections=len(list(rows(directory / "projections.jsonl"))),
            )
        )
    return made


def unit(kind: str, name: str, **counts: int) -> dict[str, Any]:
    keys = ["documents", "versions", "redactions", "turns", "projections"]
    return {"kind": kind, "name": name} | {key: counts.get(key, 0) for key in keys}


def tree_digest(root: Path) -> str:
    """Every file's relative path and bytes, in path order. The import
    opens the archive as a FileStore, which leaves its lock file
    `.store.lock`; that file alone is left out."""
    whole = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.relative_to(root).as_posix() == ".store.lock":
            continue
        whole.update(path.relative_to(root).as_posix().encode() + b"\0")
        whole.update(hashlib.sha256(path.read_bytes()).digest())
    return whole.hexdigest()


def post_raw(url: str, route: str, body: Any, timeout: float = 900) -> bytes:
    """The answer's bytes as sent; a refusal fails with its body."""
    request = Request(
        f"{url}/v1/{route}",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {ADMIN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
            answer: bytes = response.read()
            return answer
    except HTTPError as error:
        raise AssertionError(
            f"{route} refused {body}: {error.read()[:2_000]!r}"
        ) from None


def brief(record: dict[str, Any] | None) -> Any:
    if record is None:
        return None
    text = record.get("text")
    if isinstance(text, str) and len(text) > 80:
        record = record | {
            "text": f"{text[:40]!r}...{text[-20:]!r} ({len(text)} chars)"
        }
    return record


@dataclass
class Tally:
    """Records, pieces and bytes per kind, as read back."""

    records: Counter[str] = field(default_factory=Counter)
    pieces: Counter[str] = field(default_factory=Counter)
    bytes: Counter[str] = field(default_factory=Counter)

    def add(self, record: dict[str, Any]) -> None:
        self.records[record["kind"]] += 1
        if record["chunk"] == 0:
            self.pieces[record["kind"]] += 1
            self.bytes[record["kind"]] += record["size"]


def full_view(
    url: str, root: Path, conversation: str, read: Tally, expect: Tally
) -> tuple[int, str]:
    """Walks the unfiltered paged `read_records` answer and compares it,
    record by record and header by header, with the archive. Returns the
    number of pages and item 12's unit digest of the archive's restore
    stream: each turn's header then its records with the turn in front, the
    projections, the acts; SHA-256 over each item's own SHA-256."""
    whole = hashlib.sha256()

    def take(item: dict[str, Any]) -> None:
        whole.update(hashlib.sha256(compact(item)).digest())

    def pull() -> Expected | None:
        turn = next(wanted, None)
        if turn is not None:
            expected.append(turn.header["turn"])
            take(turn.header)
            for record in turn.records:
                take({"turn": turn.header["turn"], **record})
        return turn

    wanted = turns(root, conversation)
    expected: list[int] = []
    queue: list[dict[str, Any]] = []
    headers: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    body: dict[str, Any] = {"conversation_id": conversation}
    pages = 0
    while True:
        page = json.loads(post_raw(url, READ, body))
        pages += 1
        assert list(page) == PAGE_KEYS, list(page)
        on_page = {header["turn"] for header in page["turns"]}
        for header in page["turns"]:
            if header["turn"] in headers:
                assert headers[header["turn"]] == header, (conversation, header)
            else:
                headers[header["turn"]] = header
                order.append(header["turn"])
        for record in page["records"]:
            assert record["turn"] in on_page, (conversation, brief(record))
            while not queue:
                current = pull()
                assert current is not None, (conversation, "extra", brief(record))
                number = current.header["turn"]
                queue = [
                    {"turn": number, **r, "tail": None, "truncated": False}
                    for r in reversed(current.records)
                ]
                expected_header(headers, current)
            mine = queue.pop()
            if record != mine:
                raise AssertionError(
                    f"{conversation}: read {brief(record)} expected {brief(mine)}"
                )
            read.add(record)
            expect.add(mine)
        if page["continuation"] is None:
            break
        assert page["turns"], (conversation, "an empty page with a continuation")
        body = {"conversation_id": conversation, "continuation": page["continuation"]}
    assert not queue, (conversation, "records missing", brief(queue[-1]))
    while (rest := pull()) is not None:
        assert not rest.records, (conversation, "records missing", rest.header)
        expected_header(headers, rest)
    assert order == expected, (conversation, order, expected)
    for entry in projections(root, conversation):
        take(entry)
    for row in acts(root, conversation):
        take(row)
    return pages, whole.hexdigest()


def expected_header(headers: dict[int, dict[str, Any]], turn: Expected) -> None:
    number = turn.header["turn"]
    assert headers.get(number) == turn.header, (headers.get(number), turn.header)


def read_turns(
    records: str, twin: str, root: Path, conversation: str, twin_bytes: bool
) -> int:
    """`read_turns` of the whole conversation. The store that keeps records
    sends the archive's turns byte for byte: their compact JSON, non-ASCII
    raw, keys in the row's order, in the answer `{"turns": [...],
    "next_after": null}`. The twin sends the same bytes, or with
    `twin_bytes` false the same JSON (ruling K6 of the T1f plan). Returns
    the bytes."""
    body = {"conversation_id": conversation}
    kept = post_raw(records, "conversation/read_turns", body)
    wanted = [e.turn for e in turns(root, conversation, convert=False)]
    literal = compact({"turns": wanted, "next_after": None})
    differ(conversation, "the archive's turns", kept, literal)
    legacy = post_raw(twin, "conversation/read_turns", body)
    if twin_bytes:
        differ(conversation, "the twin", kept, legacy)
    else:
        assert json.loads(kept) == json.loads(legacy), (conversation, "the twin")
    return len(kept)


def differ(conversation: str, other: str, kept: bytes, them: bytes) -> None:
    if kept != them:
        at = next(
            (i for i, (a, b) in enumerate(zip(kept, them, strict=False)) if a != b),
            min(len(kept), len(them)),
        )
        raise AssertionError(
            f"{conversation}: read_turns differs from {other} at byte {at} of"
            f" {len(kept)} and {len(them)}:"
            f" {kept[at - 60 : at + 60]!r} != {them[at - 60 : at + 60]!r}"
        )


def committed(server: Server, root: Path, conversation: str, digest: str) -> None:
    """The restore the import committed holds the archive's unit digest; a
    conversation without turns went whole and left no restore."""
    status = server.post(
        RESTORE, conversation_id=conversation, restore=restore_id(conversation)
    )
    if not (root / "conversations" / conversation / "turns.jsonl").exists():
        assert status["state"] == "none", status
        return
    assert status["state"] == "committed", status
    assert status["digest"] == digest, conversation


def entries_and_trail(
    server: Server,
    trail: Callable[[str], list[tuple[datetime, str | None, list[int]]]],
    root: Path,
    conversation: str,
) -> None:
    """The projections in the store's order and the whole erasure trail in
    stored order, time, actor and turns."""
    read = server.post("conversation/read_projections", conversation_id=conversation)
    assert read["entries"] == projections(root, conversation), conversation
    assert trail(conversation) == [
        (datetime.fromisoformat(a["created_at"]), a["actor"], a["turns"])
        for a in acts(root, conversation)
    ], conversation


def file_trail(
    home: Path,
) -> Callable[[str], list[tuple[datetime, str | None, list[int]]]]:
    def read(conversation: str) -> list[tuple[datetime, str | None, list[int]]]:
        path = home / "conversations" / conversation / "redactions.jsonl"
        return [
            (datetime.fromisoformat(r["created_at"]), r["actor"], r["turns"])
            for r in rows(path)
        ]

    return read


def memory(server: Server, root: Path) -> None:
    """Every scope, document and version reads back as the archive."""
    expected = scopes(root)
    assert sorted(server.post("store/scopes")["scopes"]) == sorted(expected)
    for scope, documents in expected.items():
        paths: list[str] = []
        cursor = None
        while True:
            page = server.post("memory/list_documents", scope=scope, cursor=cursor)
            paths += [entry["path"] for entry in page["entries"]]
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert sorted(paths) == sorted(documents), scope
        for path, history in documents.items():
            document = server.post("memory/read", scope=scope, path=path)["document"]
            last = history[-1]
            assert (document["content"], document["version"], document["actor"]) == (
                last["content"],
                last["version"],
                last["actor"],
            ), (scope, path)
            assert (document["created_at"], document["updated_at"]) == (
                history[0]["created_at"],
                last["created_at"],
            ), (scope, path)
            versions = server.post("memory/versions", scope=scope, path=path)[
                "versions"
            ]
            assert sorted(versions, key=lambda v: int(v["version"])) == history, (
                scope,
                path,
            )


@dataclass
class Meter:
    """A forwarding proxy that counts what the import sends and keeps
    nothing else: bytes and requests per route, the largest restore part
    and the most items in one."""

    target: str
    sent: Counter[str] = field(default_factory=Counter)
    requests: Counter[str] = field(default_factory=Counter)
    largest_part: int = 0
    most_items: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def forward(self, method: str, path: str, raw: bytes) -> tuple[int, bytes]:
        parts = urlsplit(self.target)
        link = http.client.HTTPConnection(parts.hostname or "", parts.port, timeout=900)
        try:
            headers = {"Authorization": f"Bearer {ADMIN}"}
            if method == "POST":
                headers["Content-Type"] = "application/json"
            link.request(method, path, body=raw or None, headers=headers)
            answer = link.getresponse()
            return answer.status, answer.read()
        finally:
            link.close()

    def count(self, route: str, raw: bytes) -> None:
        with self.lock:
            self.sent[route] += len(raw)
            self.requests[route] += 1
        if route == RESTORE and raw:
            body = json.loads(raw)
            items = sum(
                len(body.get(k, []))
                for k in ("turns", "records", "projections", "redactions")
            )
            with self.lock:
                self.largest_part = max(self.largest_part, len(raw))
                self.most_items = max(self.most_items, items)


@contextmanager
def metering(target: str) -> Iterator[tuple[Meter, str]]:
    meter = Meter(target)

    class Handler(BaseHTTPRequestHandler):
        def exchange(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            meter.count(self.path.removeprefix("/v1/"), raw)
            status, answer = meter.forward(self.command, self.path, raw)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(answer)))
            self.end_headers()
            self.wfile.write(answer)

        do_GET = do_POST = exchange

        def log_message(self, *_args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}
    )
    thread.daemon = True
    thread.start()
    try:
        yield meter, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def peak(usage: Any) -> int:
    """`ru_maxrss` in bytes: bytes on macOS, KiB elsewhere."""
    return int(usage.ru_maxrss) * (1 if sys.platform == "darwin" else 1_024)


def measured(binary: Binary, out: Path) -> Binary:
    """The same binary behind a wrapper that writes the server's peak
    resident size to `out` when it stops, passing SIGTERM on."""
    wrapper = binary.directory / "measured-neosian"
    wrapper.write_text(
        f"#!{sys.executable}\n"
        "import os, signal, subprocess, sys\n"
        f"child = subprocess.Popen([{str(binary.executable)!r}, *sys.argv[1:]])\n"
        "signal.signal(signal.SIGTERM, lambda *_: child.send_signal(signal.SIGTERM))\n"
        "_, status, usage = os.wait4(child.pid, 0)\n"
        "scale = 1 if sys.platform == 'darwin' else 1024\n"
        f"open({str(out)!r}, 'w').write(str(usage.ru_maxrss * scale))\n"
        "sys.exit(os.waitstatus_to_exitcode(status))\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return Binary(wrapper, binary.directory)


@dataclass
class Imported:
    units: list[dict[str, Any]]
    seconds: float
    client_peak: int


def run_import(binary: Binary, archive: Path, url: str) -> Imported:
    """`neosian import DIR --url URL --json`, its wall time and peak."""
    out, err = binary.directory / "import.out", binary.directory / "import.err"
    command = [str(binary.executable), "import", str(archive), "--url", url, "--json"]
    started = time.monotonic()
    with out.open("wb") as stdout, err.open("wb") as stderr:
        process = subprocess.Popen(
            command,
            env=binary.environment() | {"NEOSIAN_CLIENT_TOKEN": ADMIN},
            cwd=binary.directory,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
        )
        _, status, usage = os.wait4(process.pid, 0)
    seconds = time.monotonic() - started
    text = out.read_text("utf-8")
    assert os.waitstatus_to_exitcode(status) == 0, (
        shlex.join(command),
        text[-4_000:],
        err.read_text("utf-8")[-4_000:],
    )
    made: list[dict[str, Any]] = json.loads(text)["units"]
    return Imported(made, seconds, peak(usage))


def report(record: dict[str, Any]) -> None:
    """Appends one JSON line to the file `NEOSIAN_SYNTHETIC_REPORT` names."""
    target = os.environ.get(REPORT)
    if target:
        with open(target, "a", encoding="utf-8") as file:
            file.write(json.dumps(record) + "\n")


Trail = Callable[[str], list[tuple[datetime, str | None, list[int]]]]
Starter = Callable[[Binary], AbstractContextManager[Server]]


@dataclass
class Home:
    """The archive, both stores serving what they imported, and what the
    imports reported."""

    store: str
    root: Path
    records: Server
    twin: Server
    units: list[dict[str, Any]]
    twin_units: list[dict[str, Any]]
    intact: tuple[str, str]
    meter: Meter
    trail: Trail
    twin_trail: Trail
    twin_bytes: bool


@contextmanager
def imported_home(
    base: Path,
    store: str,
    kept: tuple[Binary, Starter, Trail],
    legacy: tuple[Binary, Starter, Trail],
    twin_bytes: bool = True,
) -> Iterator[Home]:
    """Writes the profile `NEOSIAN_SYNTHETIC_PROFILE` names (the one-tenth
    by default), imports it with the CLI into the store that keeps records
    through the meter and into the twin, each into a server of its own that
    then stops, and serves both stores afresh for the reads."""
    profile = os.environ.get(PROFILE, "tenth")
    root = base / "archive"
    manifest = write_home(root, profile)
    before = tree_digest(root)
    (binary, start, trail), (twin_binary, start_twin, twin_trail) = kept, legacy
    rss = base / "server.rss"
    with start(measured(binary, rss)) as server, metering(server.url) as (meter, url):
        done = run_import(binary, root, url)
    with start_twin(twin_binary) as twin:
        plain = run_import(twin_binary, root, twin.url)
    after = tree_digest(root)
    report(
        {"profile": profile, "store": store, "manifest": asdict(manifest)}
        | {
            "import_seconds": round(done.seconds, 2),
            "bytes_sent": sum(meter.sent.values()),
        }
        | {"restore_bytes": meter.sent[RESTORE], "requests": dict(meter.requests)}
        | {"largest_part": meter.largest_part, "most_items": meter.most_items}
        | {"client_peak": done.client_peak, "server_peak": int(rss.read_text())}
        | {
            "twin_seconds": round(plain.seconds, 2),
            "twin_client_peak": plain.client_peak,
        }
    )
    with start(binary) as records, start_twin(twin_binary) as twin:
        yield Home(
            store,
            root,
            records,
            twin,
            done.units,
            plain.units,
            (before, after),
            meter,
            trail,
            twin_trail,
            twin_bytes,
        )


EVERY_KIND = {"prompt", "reply", "system", "tool_result"}
EVERY_KIND |= {"tool_call", "reasoning", "attachment", "extra"}


def check_units(home: Home) -> None:
    """Both imports report the archive's units; the archive is unchanged;
    both stores list every conversation; every part kept its bounds and
    only the conversation without turns went whole."""

    def ordered(made: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(made, key=lambda u: (str(u["kind"]), str(u["name"])))

    expected = ordered(units(home.root))
    assert ordered(home.units) == expected
    assert ordered(home.twin_units) == expected
    assert home.intact[0] == home.intact[1]
    for server in (home.records, home.twin):
        listing = server.post("store/conversations")["conversations"]
        assert sorted(listing) == conversations(home.root)
    assert home.meter.largest_part <= PART_BYTES
    assert home.meter.most_items <= PART_ITEMS
    whole = [u for u in expected if u["kind"] == "conversation" and u["turns"] == 0]
    assert home.meter.requests["store/restore_conversation"] == len(whole) == 1
    assert home.meter.requests[RESTORE] >= 2 * (len(conversations(home.root)) - 1)


def check_full_view(home: Home) -> None:
    """Every conversation's full view, record by record, its counts and
    bytes per kind, and the unit digest its restore committed."""
    read, expect = Tally(), Tally()
    pages = 0
    for conversation in conversations(home.root):
        walked, digest = full_view(
            home.records.url, home.root, conversation, read, expect
        )
        committed(home.records, home.root, conversation, digest)
        pages += walked
    assert read == expect
    assert set(read.records) == EVERY_KIND
    counts = {k: dict(getattr(read, k)) for k in ("records", "pieces", "bytes")}
    report({"store": home.store, "pages": pages} | counts)


def check_read_turns(home: Home) -> None:
    sent = sum(
        read_turns(home.records.url, home.twin.url, home.root, c, home.twin_bytes)
        for c in conversations(home.root)
    )
    report({"store": home.store, "read_turns_bytes": sent})


def check_entries_and_trails(home: Home) -> None:
    for conversation in conversations(home.root):
        entries_and_trail(home.records, home.trail, home.root, conversation)
        entries_and_trail(home.twin, home.twin_trail, home.root, conversation)


def check_memory(home: Home) -> None:
    memory(home.records, home.root)
    memory(home.twin, home.root)
