"""A recalled turn built from DESIGN §40.54 alone, and the ways to ask for it.

The turn is written twice from the same messages: whole, through
`append_turn`, to a server without records, and as records, through
`write_records`, split by item 3.4's rule as written here, to a server that
keeps them. A capture proxy keeps every request the binary sends and the
bytes it is answered. Nothing here is taken from what the binary prints.
"""

from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from tests.contract.records.support import record, write
from tests.contract.support import Binary, Server

TEXT_BYTES = 65_536
WINDOW = 4_096
LINE = "alpha beta gamma delta\n"
CALL = "toolu_01"


def width(char: str) -> int:
    """A character's bytes in a compact JSON string."""
    if char in '"\\\b\f\n\r\t':
        return 2
    if ord(char) < 0x20:
        return 6
    return len(char.encode())


def head_of(text: str, size: int) -> str:
    """The longest prefix encoding to at most `size` bytes, quotes included."""
    used = 2
    for at, char in enumerate(text):
        used += width(char)
        if used > size:
            return text[:at]
    return text


def tail_of(text: str, size: int) -> str:
    return head_of(text[::-1], size)[::-1]


def split(text: str) -> list[str]:
    """Item 3.4: the longest prefix within 65,536 encoded bytes, cut after
    the last line feed in its last 4,096 UTF-8 bytes, else after the last
    whitespace there, else where it stands."""
    chunks: list[str] = []
    rest = text
    while True:
        prefix = head_of(rest, TEXT_BYTES)
        if len(prefix) < len(rest):
            window, used = len(prefix), 0
            while window > 0 and used + len(prefix[window - 1].encode()) <= WINDOW:
                window -= 1
                used += len(prefix[window].encode())
            feed = prefix.rfind("\n", window)
            spaces = [n for n in range(window, len(prefix)) if prefix[n].isspace()]
            if feed >= 0:
                prefix = prefix[: feed + 1]
            elif spaces:
                prefix = prefix[: spaces[-1] + 1]
        chunks.append(prefix)
        rest = rest[len(prefix) :]
        if not rest:
            return chunks


def result_text(lines: int) -> str:
    block = LINE * lines
    return f"HEAD-CANARY {block}MIDDLE-CANARY\n{block}TAIL-CANARY"


@dataclass
class Turn:
    """One turn: a prompt, one call with long arguments, its result, a reply."""

    prompt: str
    arguments: dict[str, Any]
    result: str
    reply: str = "All done."

    @property
    def arguments_text(self) -> str:
        return json.dumps(self.arguments, ensure_ascii=False, separators=(",", ":"))

    def messages(self) -> list[dict[str, Any]]:
        call = {"id": CALL, "name": "Write", "arguments": self.arguments}
        return [
            {"role": "user", "content": self.prompt},
            {"role": "assistant", "content": None, "tool_calls": [call]},
            {"role": "tool", "content": self.result, "tool_call_id": CALL},
            {"role": "assistant", "content": self.reply},
        ]

    def pieces(self) -> list[list[str]]:
        """Each piece's chunks, in record order."""
        texts = (self.prompt, self.arguments_text, self.result, self.reply)
        return [split(text) for text in texts]

    def records(self, chunked: list[list[str]] | None = None) -> list[dict[str, Any]]:
        """Item 3.3's records, chunked by the split rule unless given."""
        chunked = chunked or self.pieces()
        shapes: list[tuple[str, str, str, dict[str, Any]]] = [
            ("user", "prompt", "text", {}),
            (
                "assistant",
                "tool_call",
                "none",
                {"call": 0, "call_id": CALL, "name": "Write"},
            ),
            ("tool", "tool_result", "text", {"call_id": CALL}),
            ("assistant", "reply", "text", {}),
        ]
        items: list[dict[str, Any]] = []
        for message, ((role, kind, form, named), chunks) in enumerate(
            zip(shapes, chunked, strict=True)
        ):
            size = sum(len(chunk.encode()) for chunk in chunks)
            for at, chunk in enumerate(chunks):
                content = form if at == 0 else None
                items.append(
                    record(
                        len(items),
                        message,
                        role,
                        kind,
                        chunk,
                        content=content,
                        chunk=at,
                        size=size,
                        **named,
                    )
                )
        return items

    def first(self, kind: str, records: list[dict[str, Any]] | None = None) -> int:
        items = records or self.records()
        return next(int(item["seq"]) for item in items if item["kind"] == kind)


def store(server: Server, conversation: str, items: list[dict[str, Any]]) -> None:
    """The records in parts of at most 32, the last sealing the turn."""
    parts = [items[at : at + 32] for at in range(0, len(items), 32)]
    for at, part in enumerate(parts):
        write(server, conversation, "d-recall", part, seal=at == len(parts) - 1)


def run(
    binary: Binary, *args: str, env: dict[str, str] | None = None, stdin: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary.executable), *args],
        cwd=binary.directory,
        env={**binary.environment(), **(env or {})},
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


def mcp(
    binary: Binary,
    flags: list[str],
    calls: list[dict[str, Any]],
    env: dict[str, str] | None = None,
) -> list[Any]:
    """`recall_turn` calls over stdio; each answer's `result`."""
    requests: list[dict[str, Any]] = [
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "contract", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ]
    for number, arguments in enumerate(calls, 1):
        params = {"name": "recall_turn", "arguments": arguments}
        requests.append(
            {"jsonrpc": "2.0", "id": number, "method": "tools/call", "params": params}
        )
    stdin = "".join(json.dumps(request) + "\n" for request in requests)
    process = run(
        binary, "mcp", *flags, "--scope", "user:contract", env=env, stdin=stdin
    )
    assert process.returncode == 0, process.stderr
    answers = [json.loads(line) for line in process.stdout.splitlines()]
    return [answer["result"] for answer in answers[1:]]


def text(result: dict[str, Any]) -> str:
    return "\n".join(block["text"] for block in result["content"])


@dataclass
class Capture:
    """Every request the binary sends a server, and the bytes answered."""

    upstream: str
    url: str = ""
    exchanges: list[tuple[str, str, bytes, int]] = field(default_factory=list)

    def answered(self) -> int:
        return sum(size for *_, size in self.exchanges)


@contextmanager
def capture(server: Server) -> Iterator[Capture]:
    held = Capture(server.url)
    opener = build_opener(ProxyHandler({}))
    skipped = {
        "host",
        "content-length",
        "connection",
        "accept-encoding",
        "transfer-encoding",
    }

    class Forward(BaseHTTPRequestHandler):
        def forward(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            headers = {
                k: v for k, v in self.headers.items() if k.lower() not in skipped
            }
            sent = Request(
                held.upstream + self.path,
                data=body or None,
                headers=headers,
                method=self.command,
            )
            try:
                response = opener.open(sent, timeout=60)
            except HTTPError as error:
                response = error
            with response:
                data = response.read()
                status, answered = response.status, list(response.headers.items())
            held.exchanges.append((self.command, self.path, body, len(data)))
            self.send_response(status)
            for key, value in answered:
                if key.lower() not in skipped:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = forward
        do_POST = forward

        def log_message(self, *_: Any) -> None:
            return

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), Forward)
    held.url = f"http://127.0.0.1:{proxy.server_address[1]}"
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    try:
        yield held
    finally:
        proxy.shutdown()
        proxy.server_close()
