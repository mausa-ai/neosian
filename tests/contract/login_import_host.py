"""The host side of an import at login, written from the host contract
(cloud.neosian.com `docs/product/design/login-import.md`) with no product
code: discovery's `import_endpoint`, the intent at device start, Begin,
the transfer (`store/restore_scope` and `store/restore_records` in parts),
typed refusals, commit, status and discard.

It extends the HTTPS `Host` of `test_machine_login.py`. Every request is
logged raw (method, path, Authorization, body bytes). A conversation's
restore is modelled from DESIGN §40.54 item 12: items keyed by turn, record,
projection and act; a repeat must equal what is held; the answer stands at
the position after the last item received; the unit digest of
`records/restoring.py` is computed over the items as received. The host
can refuse a unit by name, lose the answer to a request after applying it,
and name a transfer URL on another origin.
"""

from __future__ import annotations

import hashlib
import json
import re
import ssl
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from tests.contract.records.restoring import unit_digest
from tests.contract.test_machine_login import Host
from tests.contract.test_transport import CERTS

SESSIONS = "/import-sessions"
WIRE = re.compile(
    r"^/import-sessions/([0-9a-f]{32})/wire/v1/store/(restore_scope|restore_records)$"
)
ONE = re.compile(r"^/import-sessions/([0-9a-f]{32})(/commit)?$")
REASONS = {"unit_too_large", "unit_refused", "excluded"}
# Each item's keys in item 3's order: the encoder leaves a null out, the
# digest covers every key (records/snapshotting.py, `present`).
ORDER = {
    "h": ("turn", "created_at", "actor", "redacted", "records", "bytes"),
    "r": ("turn", "seq", "message", "role", "kind", "content", "block")
    + ("call", "call_id", "name", "chunk", "size", "text"),
    "p": ("turn", "kind", "text", "span"),
    "a": ("conversation_id", "turns", "actor", "created_at"),
}


@dataclass
class Exchange:
    method: str
    path: str
    auth: str | None
    raw: bytes

    @property
    def body(self) -> Any:
        return json.loads(self.raw) if self.raw else None


def kit(status: int, code: str) -> tuple[int, dict[str, Any]]:
    """The host's own envelope (the kit's)."""
    return status, {"code": code, "message": code, "details": {}, "request_id": "r"}


def wire(status: int, code: str, **details: Any) -> tuple[int, dict[str, Any]]:
    """The core's wire envelope."""
    return status, {"error": {"code": code, "message": code, "details": details}}


@dataclass
class Restore:
    """One conversation's restore as the host stages it."""

    keys: dict[Any, bytes] = field(default_factory=dict)
    items: list[Any] = field(default_factory=list)
    counts: dict[int, int] = field(default_factory=dict)
    state: str = "none"
    position: dict[str, Any] | None = None
    digest: str | None = None

    def answer(self, restore: str) -> dict[str, Any]:
        return {
            "restore": restore,
            "state": self.state,
            "position": self.position,
            "stored_bytes": 0,
            "freed_bytes": 0,
            "digest": self.digest,
        }

    def take(self, key: Any, item: dict[str, Any]) -> bool:
        """False when a repeat differs from what is held."""
        item = {k: item.get(k) for k in ORDER[key[0]]}
        raw = json.dumps(item, separators=(",", ":"), ensure_ascii=False).encode()
        if key in self.keys:
            return self.keys[key] == raw
        self.keys[key] = raw
        self.items.append(item)
        at = dict(self.position or {"turn": 0, "seq": 0, "complete": True})
        at.setdefault("projections", 0)
        at.setdefault("redactions", 0)
        if key[0] == "h":
            self.counts[item["turn"]] = item["records"]
            at |= {"turn": item["turn"], "seq": 0}
            at["complete"] = item["redacted"] or item["records"] == 0
        elif key[0] == "r":
            at |= {"turn": item["turn"], "seq": item["seq"] + 1}
            at["complete"] = item["seq"] + 1 == self.counts.get(item["turn"])
        elif key[0] == "p":
            at["projections"] = key[1] + 1
        else:
            at["redactions"] = key[1] + 1
        self.position = {
            k: at[k] for k in ("turn", "seq", "complete", "projections", "redactions")
        }
        return True


def items_of(body: dict[str, Any]) -> list[tuple[Any, dict[str, Any]]]:
    """A part's items in stream order, each with its key."""
    out: list[tuple[Any, dict[str, Any]]] = []
    records = list(body.get("records", []))
    for head in body.get("turns", []):
        while records and records[0]["turn"] < head["turn"]:
            r = records.pop(0)
            out.append((("r", r["turn"], r["seq"]), r))
        out.append((("h", head["turn"]), head))
    out.extend((("r", r["turn"], r["seq"]), r) for r in records)
    start = body.get("projections_from") or 0
    out.extend((("p", start + i), p) for i, p in enumerate(body.get("projections", [])))
    start = body.get("redactions_from") or 0
    out.extend((("a", start + i), a) for i, a in enumerate(body.get("redactions", [])))
    return out


@dataclass
class Session:
    id: str
    token: str
    status: str = "open"
    units: dict[tuple[str, str], str] = field(default_factory=dict)
    restores: dict[str, Restore] = field(default_factory=dict)
    commit: dict[str, Any] | None = None
    reads: int = 0


@dataclass
class ImportHost(Host):
    offers_import: bool = True
    # The browser approval includes the import whenever the device start asked.
    approve_import: bool = True
    # A unit name the host refuses at its first data request: (status, code).
    refuse: dict[str, tuple[int, str]] = field(default_factory=dict)
    # Lose the answer to the n-th request of a kind ("data", "commit",
    # "status") after applying it, counted from 1 over the host's life.
    lose: dict[str, set[int]] = field(default_factory=dict)
    result: str = "succeeded"
    queued_reads: int = 1
    # A transfer URL on another origin, when set.
    elsewhere: str | None = None
    log: list[Exchange] = field(default_factory=list)
    intents: list[Any] = field(default_factory=list)
    authorized: set[str] = field(default_factory=set)
    sessions: dict[str, Session] = field(default_factory=dict)
    counted: dict[str, int] = field(default_factory=dict)
    consent: bool = False

    def imports(self) -> list[Exchange]:
        return [e for e in self.log if e.path.startswith(SESSIONS)]

    def lost(self, kind: str) -> bool:
        self.counted[kind] = self.counted.get(kind, 0) + 1
        return self.counted[kind] in self.lose.get(kind, set())

    def answer(
        self, method: str, path: str, auth: str | None, raw: bytes
    ) -> tuple[int, Any] | None:
        """None means: close the connection without an answer."""
        body = json.loads(raw) if raw else {}
        if path == "/host":
            code, data = self.reply(path, auth, None, body)
            if self.offers_import:
                data["import_endpoint"] = self.url + SESSIONS
            return code, data
        if path == "/device":
            self.intents.append(body.get("import"))
            self.consent = "import" in body and self.approve_import
        if path == "/token":
            code, data = self.reply(path, auth, None, body)
            if code == 200 and self.consent:
                self.authorized.add(data["secret"])
            return code, data
        if not path.startswith(SESSIONS):
            return self.reply(path, auth, None, body)
        token = (auth or "").removeprefix("Bearer ")
        if token not in self.tokens:
            return 401, {}
        if path == SESSIONS and method == "POST":
            return self.begin(token)
        if found := WIRE.match(path):
            return self.transfer(found.group(1), found.group(2), token, raw, body)
        if found := ONE.match(path):
            session = self.sessions.get(found.group(1))
            if session is None or session.token != token:
                return kit(404, "not_found")
            if found.group(2):
                return self.commit(session, body)
            if method == "DELETE":
                return self.discard(session)
            return self.progress(session)
        return 404, {}

    def started(self, session: Session) -> dict[str, Any]:
        base = self.elsewhere or f"{self.url}{SESSIONS}/{session.id}"
        received = [
            {"kind": kind, "name": name, "sha256": sha}
            for (kind, name), sha in session.units.items()
        ]
        return {
            "id": session.id,
            "transfer_url": f"{base}/wire",
            "expires_at": "2026-11-29T12:00:00Z",
            "limits": {"unit_bytes": 4_194_304, "units": 10_000}
            | {"storage_bytes": 1 << 30, "workspaces": 256},
            "received": received,
        }

    def begin(self, token: str) -> tuple[int, Any]:
        if token not in self.authorized:
            return kit(403, "import_not_authorized")
        for session in self.sessions.values():
            if session.token == token and session.status == "open":
                return 200, self.started(session)
        session = Session(uuid.uuid4().hex, token)
        self.sessions[session.id] = session
        return 201, self.started(session)

    def transfer(
        self, sid: str, route: str, token: str, raw: bytes, body: dict[str, Any]
    ) -> tuple[int, Any] | None:
        session = self.sessions.get(sid)
        if session is None or session.token != token or session.status != "open":
            return wire(410, "session_expired")
        if route == "restore_scope":
            name, sha = body["scope"], hashlib.sha256(raw).hexdigest()
            if name in self.refuse:
                return wire(*self.refuse[name])
            if session.units.get(("scope", name), sha) != sha:
                return wire(409, "unit_conflict")
            session.units[("scope", name)] = sha
            return None if self.lost("data") else (200, {})
        name, restore = body["conversation_id"], body["restore"]
        staged = session.restores.setdefault(name, Restore())
        if body.get("discard"):
            staged.state, staged.position = "discarded", None
            return 200, staged.answer(restore)
        if len(body) == 2:
            return 200, staged.answer(restore)
        if name in self.refuse:
            return wire(*self.refuse[name])
        if staged.state not in ("none", "open"):
            return wire(400, "agent_conversation_conflict", reason="restore_closed")
        for key, item in items_of(body):
            if not staged.take(key, item):
                return wire(
                    400, "agent_conversation_conflict", reason="restore_mismatch"
                )
        staged.state = "open"
        if body.get("commit"):
            staged.state, staged.digest = "committed", unit_digest(staged.items)
            session.units[("conversation", name)] = staged.digest
        return None if self.lost("data") else (200, staged.answer(restore))

    def commit(self, session: Session, body: dict[str, Any]) -> tuple[int, Any] | None:
        units, omitted = body.get("units"), body.get("omitted", [])
        well_formed = (
            set(body) <= {"units", "omitted", "personal"}
            and isinstance(units, list)
            and units
            and all(set(u) == {"kind", "name", "sha256", "bytes"} for u in units)
            and all(isinstance(u["bytes"], int) and u["bytes"] > 0 for u in units)
            and all(re.fullmatch("[0-9a-f]{64}", u["sha256"]) for u in units)
            and all(set(o) == {"kind", "name", "reason"} for o in omitted)
            and all(o["reason"] in REASONS for o in omitted)
        )
        if not well_formed or not isinstance(units, list):
            return kit(422, "value_error")
        if session.commit is not None:
            return (
                (202, {"id": session.id})
                if session.commit == body
                else kit(409, "units_mismatch")
            )
        listed = {(u["kind"], u["name"]): u["sha256"] for u in units}
        named = {(o["kind"], o["name"]) for o in omitted}
        if listed != session.units or named & set(session.units):
            return kit(409, "units_mismatch")
        session.commit, session.status = body, "queued"
        return None if self.lost("commit") else (202, {"id": session.id})

    def progress(self, session: Session) -> tuple[int, Any] | None:
        if self.lost("status"):
            return None
        data: dict[str, Any] = {"id": session.id, "status": session.status}
        data |= {"total_units": len(session.units), "completed_units": 0}
        data |= {
            "error_code": None,
            "waiting_reason": None,
            "resume_at": None,
            "targets": None,
        }
        if session.commit is None:
            return 200, data
        session.reads += 1
        if session.reads <= self.queued_reads:
            return 200, data
        session.status = self.result
        if self.result == "waiting":
            data |= {
                "waiting_reason": "daily_quota",
                "resume_at": "2026-11-29T00:00:00Z",
            }
        if self.result == "failed":
            data |= {"error_code": "unit_refused"}
        if self.result == "succeeded":
            personal = session.commit.get("personal")
            data["targets"] = [
                (
                    {"source": name, "mount": "me", "profile": None}
                    if name == personal
                    else {"source": name, "mount": "workspace", "profile": f"w-{n}"}
                )
                for n, (kind, name) in enumerate(sorted(session.units))
                if kind == "scope"
            ]
            data["completed_units"] = len(session.units)
        data["status"] = self.result
        return 200, data

    def discard(self, session: Session) -> tuple[int, Any]:
        if session.status == "open":
            session.status = "discarded"
            return 204, None
        return kit(
            409, "import_running" if session.status == "queued" else "import_finished"
        )


@contextmanager
def serving(host: ImportHost) -> Iterator[ImportHost]:
    """`host` over HTTPS on a port of its own, every request logged raw."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.respond()

        def do_POST(self) -> None:
            self.respond()

        def do_DELETE(self) -> None:
            self.respond()

        def respond(self) -> None:
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            auth = self.headers.get("Authorization")
            host.log.append(Exchange(self.command, self.path, auth, raw))
            answered = host.answer(self.command, self.path, auth, raw)
            if answered is None:
                self.close_connection = True
                self.connection.close()
                return
            code, data = answered
            payload = b"" if code in (204, 304) else json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(CERTS / "server.pem", CERTS / "server-key.pem")
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    host.url = f"https://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield host
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
