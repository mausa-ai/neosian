"""A forwarding stub in front of a real `neosian serve` (the T1e1c slice of
the T1e1 plan; the forwarding proxy of the T1c2 matrix is the precedent).
Every request is logged with its raw body, then answered by the first rule
that matches it or forwarded unchanged; a forwarded request logs the
server's own answer, so a test knows what the server applied even when the
lander never read the answer. A rule can answer in the server's place,
expire the draft at the server first, forward and drop the connection (a
lost answer), forward and rewrite the answer, or hold the request until the
test releases it, before or after the server applied it, or close the
connection without forwarding it (a request that never arrived), or hold it
and then forward and answer it as usual (a slow destination)."""

from __future__ import annotations

import http.client
import json
import socket
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from tests.contract.support import ADMIN

PREFIX = "/v1/"


@dataclass
class Exchange:
    """One request as the stub saw it, and how it was answered."""

    route: str
    raw: bytes
    body: Any
    number: int
    nth: int
    run: int
    status: int | None = None
    answer: Any = None
    forwarded: bool = False


@dataclass
class Gate:
    """A request held until `release`; `reached` is set once it is held."""

    reached: threading.Event = field(default_factory=threading.Event)
    released: threading.Event = field(default_factory=threading.Event)

    def release(self) -> None:
        self.released.set()


@dataclass
class Rule:
    """What to do with requests `when` matches, at most `times` of them.
    `kind` is `answer`, `expire`, `cut`, `drop`, `rewrite` (with
    `rewrite_status` in place of the server's status when set),
    `hold_before`, `hold_after` or `hold` (held, then forwarded and
    answered)."""

    when: Callable[[Exchange], bool]
    kind: str
    status: int = 200
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    rewrite: Callable[[Any], Any] | None = None
    rewrite_status: int | None = None
    gate: Gate | None = None
    times: int = 1


def route_is(route: str) -> Callable[[Exchange], bool]:
    return lambda exchange: exchange.route == route


def conflict(conversation: str, reason: str, **details: Any) -> dict[str, Any]:
    """A conflict as the server's envelope writes it."""
    return {
        "error": {
            "code": "agent_conversation_conflict",
            "message": f"Conversation conflict on '{conversation}': {reason}",
            "details": {"conversation_id": conversation, "reason": reason, **details},
        }
    }


class Stub:
    def __init__(self, target: str) -> None:
        parts = urlsplit(target)
        self.host, self.port = parts.hostname or "127.0.0.1", int(parts.port or 80)
        self.log: list[Exchange] = []
        self.rules: list[Rule] = []
        self.lock = threading.Lock()
        self.run = 0
        self.counts: dict[str, int] = {}
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                stub.exchange(self)

            def do_POST(self) -> None:
                stub.exchange(self)

            def log_message(self, *_args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}
        )
        self.thread.daemon = True
        self.thread.start()

    def close(self) -> None:
        for rule in self.rules:
            if rule.gate is not None:
                rule.gate.release()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def next_run(self) -> None:
        """A new lander run: `Exchange.number` (every request of the run)
        and `Exchange.nth` (the requests of one route) count from 1 again."""
        with self.lock:
            self.run += 1
            self.counts = {}

    def add(self, rule: Rule) -> Rule:
        with self.lock:
            self.rules.append(rule)
        return rule

    def forward(self, method: str, path: str, raw: bytes) -> tuple[int, bytes]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=30)
        try:
            headers = {"Authorization": f"Bearer {ADMIN}"}
            if method == "POST":
                headers["Content-Type"] = "application/json"
            connection.request(method, path, body=raw or None, headers=headers)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def discard(self, conversation: str, draft: str) -> None:
        """Drops a draft at the server, as its sweep of stale drafts would."""
        body: dict[str, Any] = {"conversation_id": conversation, "draft": draft}
        body |= {"records": [], "discard": True}
        status, answer = self.forward(
            "POST", PREFIX + "conversation/write_records", json.dumps(body).encode()
        )
        assert status == 200, answer

    def exchange(self, handler: BaseHTTPRequestHandler) -> None:
        assert handler.headers["Authorization"] == f"Bearer {ADMIN}"
        length = int(handler.headers.get("Content-Length") or 0)
        raw = handler.rfile.read(length) if length else b""
        route = handler.path.removeprefix(PREFIX)
        with self.lock:
            self.counts[route] = self.counts.get(route, 0) + 1
            self.counts[""] = self.counts.get("", 0) + 1
            exchange = Exchange(
                route=route,
                raw=raw,
                body=json.loads(raw) if raw else None,
                number=self.counts[""],
                nth=self.counts[route],
                run=self.run,
            )
            self.log.append(exchange)
            rule = next(
                (r for r in self.rules if r.times > 0 and r.when(exchange)), None
            )
            if rule is not None:
                rule.times -= 1
        kind = rule.kind if rule is not None else "forward"
        if kind in ("answer", "expire"):
            assert rule is not None
            if kind == "expire":
                self.discard(exchange.body["conversation_id"], exchange.body["draft"])
            exchange.status, exchange.answer = rule.status, rule.body
            body = rule.body if isinstance(rule.body, str) else json.dumps(rule.body)
            self.reply(handler, rule.status, body.encode(), rule.headers)
            return
        if kind == "cut":
            self.drop(handler)
            return
        if kind == "hold_before":
            assert rule is not None and rule.gate is not None
            rule.gate.reached.set()
            rule.gate.released.wait(60)
            self.drop(handler)
            return
        if kind == "hold":
            assert rule is not None and rule.gate is not None
            rule.gate.reached.set()
            rule.gate.released.wait(60)
        status, answer = self.forward(handler.command, handler.path, raw)
        exchange.status, exchange.forwarded = status, True
        exchange.answer = json.loads(answer) if answer else None
        if kind == "hold_after":
            assert rule is not None and rule.gate is not None
            rule.gate.reached.set()
            rule.gate.released.wait(60)
            self.drop(handler)
            return
        if kind == "drop":
            self.drop(handler)
            return
        if kind == "rewrite":
            assert rule is not None and rule.rewrite is not None
            exchange.answer = rule.rewrite(exchange.answer)
            answer = json.dumps(exchange.answer).encode()
            status = rule.rewrite_status or status
            exchange.status = status
        self.reply(handler, status, answer, {})

    @staticmethod
    def reply(
        handler: BaseHTTPRequestHandler,
        status: int,
        body: bytes,
        headers: dict[str, str],
    ) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        for name, value in headers.items():
            handler.send_header(name, value)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    @staticmethod
    def drop(handler: BaseHTTPRequestHandler) -> None:
        """Closes the connection without an answer."""
        handler.close_connection = True
        with suppress(OSError):
            handler.connection.shutdown(socket.SHUT_RDWR)

    def records(self, conversation: str | None = None) -> list[Exchange]:
        """The `write_records` requests, of one conversation or all."""
        return [
            exchange
            for exchange in self.log
            if exchange.route == "conversation/write_records"
            and (
                conversation is None or exchange.body["conversation_id"] == conversation
            )
        ]


@contextmanager
def forwarding(target: str) -> Iterator[Stub]:
    stub = Stub(target)
    try:
        yield stub
    finally:
        stub.close()


def resumed_without_resending(log: list[Exchange]) -> None:
    """The resume contract, checked on the raw log (rulings E9 of the T1e1
    plan): for each draft, against the `next_seq` the server holds (known
    from every answer it gave, read by the lander or not), no part repeats a
    record below `next_seq - 1`; record `next_seq - 1` is repeated only by
    the first part after a probe that answered `open` (E9's check), and that
    part starts there; every other part starts at `next_seq`."""
    held: dict[str, int] = {}
    probed: dict[str, int | None] = {}
    for exchange in log:
        if exchange.route != "conversation/write_records":
            continue
        body = exchange.body
        draft = body["draft"]
        answer = exchange.answer if exchange.forwarded else None
        if body.get("discard"):
            held[draft] = 0
            probed.pop(draft, None)
            continue
        if not body["records"]:
            open_at = None
            if isinstance(answer, dict) and answer.get("state") == "open":
                open_at = int(answer["next_seq"])
            probed[draft] = open_at
            if isinstance(answer, dict) and answer.get("state") == "none":
                held[draft] = 0
            continue
        first = body["records"][0]["seq"]
        stored = held.get(draft, 0)
        after_probe = probed.pop(draft, None)
        if after_probe is not None and after_probe > 0:
            assert first == after_probe - 1, (draft, first, after_probe)
        elif first < stored:
            raise AssertionError(f"draft {draft} sent seq {first} again below {stored}")
        else:
            assert first == stored, (draft, first, stored)
        assert first >= stored - 1, (draft, first, stored)
        if isinstance(answer, dict) and answer.get("state") in ("open", "sealed"):
            held[draft] = int(answer["next_seq"])
