"""The `serve` cases of the corpus (R2 slice A, #368): the wire's goldens
and probes (`tests/unit/server/test_wire_golden.py`, `test_wire_probes.py`)
re-spelled as exchange lists over the seeded home, so both state
processes answer them and leave the same home; the token table and the
allowance; the grammar tier's refusals as one-shot runs. Since slice B
(#370, #371) the `/mcp` surface too: the handshake-era session over the
project layout both doors derive from the working directory, its tools,
prompts and refusals, the requests that present no session, and a
session's writes read back through the wire."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from tests.differential.harness import SESSION_HEADER, TOKEN, Case, Exchange

SERVE: Final = ("serve", "--root", "{home}", "--port", "{port}")
TOKEN_ENV: Final = {"NEOSIAN_SERVE_TOKEN": TOKEN}
_ARCHIVE: Final = "{user}/proj:archive"
_HI: Final = {"role": "user", "content": "hi"}
_T0: Final = "2026-08-19T10:00:00Z"
_MCP_HEADERS: Final = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
_INITIALIZE: Final = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "differential", "version": "0"},
    },
}
_INITIALIZED: Final = {"jsonrpc": "2.0", "method": "notifications/initialized"}
_OVER_MCP: Final = "notes/over-mcp"


def _post(
    path: str,
    body: Mapping[str, Any] | None = None,
    token: str | None = TOKEN,
    **headers: str,
) -> Exchange:
    return Exchange("POST", f"/v1/{path}", {} if body is None else body, token, headers)


def _get(path: str, token: str | None = TOKEN) -> Exchange:
    return Exchange("GET", path, None, token)


def _raw(path: str, content: bytes, **headers: str) -> Exchange:
    return Exchange("POST", f"/v1/{path}", content, TOKEN, headers)


def _serve(
    name: str,
    *exchanges: Exchange,
    writes: bool = False,
    env: Mapping[str, str] = TOKEN_ENV,
) -> Case:
    return Case(f"serve {name}", SERVE, http=exchanges, env=env, writes=writes)


def _without(data: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    return {k: v for k, v in data.items() if k not in keys}


def _handshake() -> Case:
    read = {"scope": "user:a", "path": "notes/a"}
    return _serve(
        "handshake",
        _get("/health", token=None),
        _get("/v1/capabilities"),
        _get("/v1/capabilities", token=None),
        _post("memory/read", read, token=None),
        _post("memory/read", read, token="wrong"),
        Exchange("GET", "/v1/memory/read"),
        _post("memory/nope"),
        Exchange("POST", "/nope", {}),
        Exchange("GET", "/nope", None, None),
    )


def _memory() -> list[Case]:
    a = {"scope": "user:a", "path": "notes/a"}
    lifecycle = _serve(
        "memory lifecycle",
        _post("memory/write", {**a, "content": "x"}),
        _post("memory/read", a),
        _post("memory/read", {"scope": "user:a", "path": "notes/none"}),
        _post("memory/write", {**a, "content": "y", "actor": "me"}),
        _post("memory/list_documents", {"scope": "user:a"}),
        _post("memory/versions", a),
        _post("memory/rename", {"scope": "user:a", "src": "notes/a", "dst": "notes/b"}),
        _post("memory/delete", {"scope": "user:a", "path": "notes/b"}),
        _post("memory/delete", {"scope": "user:a", "path": "notes/b"}),
        _post(
            "memory/write",
            {
                "scope": "{proj}",
                "path": "notes/wire",
                "content": "# Wire\n\nover the wire\n",
            },
        ),
        writes=True,
    )
    profile = {"scope": "{user}", "path": "profile"}
    listings = _serve(
        "listings paged",
        _post("memory/list_documents", {"scope": _ARCHIVE, "limit": 10}),
        _post(
            "memory/list_documents",
            {"scope": _ARCHIVE, "limit": 10, "cursor": "{cursor:0}"},
        ),
        _post(
            "memory/list_documents", {"scope": _ARCHIVE, "prefix": "alpha/", "limit": 5}
        ),
        _post("memory/list_documents", {"scope": _ARCHIVE, "limit": 0}),
        _post(
            "memory/list_documents", {"scope": _ARCHIVE, "limit": 0, "cursor": "abc"}
        ),
        _post("memory/versions", {**profile, "limit": 2}),
        _post("memory/versions", {**profile, "limit": 2, "cursor": "{cursor:5}"}),
        _post("memory/versions", {**profile, "cursor": "{cursor:0}"}),
        _post("memory/history", {"scope": _ARCHIVE, "limit": 20}),
        _post(
            "memory/history", {"scope": _ARCHIVE, "limit": 20, "cursor": "{cursor:8}"}
        ),
        _post("memory/history", {"scope": "{user}", "since": "2000-01-01T00:00:00Z"}),
        _post(
            "memory/history", {"scope": "{user}", "since": "2100-01-01T00:00:00+02:00"}
        ),
        _post("memory/history", {"scope": "{user}", "cursor": "abc"}),
        _post("memory/history", {"scope": "{user}", "limit": -1}),
        _post("memory/redactions", {"scope": "{proj}", "limit": 1}),
        _post(
            "memory/redactions", {"scope": "{proj}", "since": "2100-01-01T00:00:00Z"}
        ),
        _post("memory/list_documents", {"scope": "{proj}", "prefix": "skills/"}),
        _post("memory/versions", {"scope": "{proj}", "path": "scratch"}),
        _post("memory/history", {"scope": "{proj}"}),
        _post("memory/history", {"scope": "{proj}", "limit": 0}),
        _post(
            "memory/list_documents", {"scope": "{proj}", "prefix": None, "limit": None}
        ),
    )
    ledger = _serve(
        "ledger",
        _post("memory/write", {"scope": "user:l", "path": "notes/a", "content": "x"}),
        _post("memory/redact", {"scope": "user:l", "path": "notes/a"}),
        _post("memory/redactions", {"scope": "user:l"}),
        _post("memory/history", {"scope": "user:l"}),
        _post("memory/write", {"scope": "user:l", "path": "a", "content": "x"}),
        _post("memory/write", {"scope": "user:l", "path": "b", "content": "x"}),
        _post("memory/redact", {"scope": "user:l"}),
        _post("memory/redactions", {"scope": "user:l"}),
        _post("memory/read", {"scope": "user:l", "path": "a"}),
        _post("memory/redact", {"scope": "user:l", "path": "never"}),
        writes=True,
    )
    return [lifecycle, listings, ledger]


def _conversation() -> list[Case]:
    rich = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "a"},
            {"type": "image", "url": "https://x/y.png"},
        ],
        "reasoning": "why",
        "tool_calls": [
            {
                "id": "c1",
                "name": "memory",
                "arguments": {"command": "view"},
                "extra": {"k": 1},
            }
        ],
        "extra": {"provider": "x"},
    }
    lifecycle = _serve(
        "conversation lifecycle",
        _post(
            "conversation/append_turn", {"conversation_id": "wire-1", "messages": [_HI]}
        ),
        _post("conversation/read_turns", {"conversation_id": "wire-1"}),
        _post("conversation/last_turn_number", {"conversation_id": "wire-1"}),
        _post("conversation/last_turn_number", {"conversation_id": "never"}),
        _post(
            "conversation/append_projections",
            {
                "conversation_id": "wire-1",
                "entries": [{"turn": 1, "kind": "log", "text": "t"}],
            },
        ),
        _post("conversation/read_projections", {"conversation_id": "wire-1"}),
        _post(
            "conversation/append_turn",
            {"conversation_id": "wire-1", "messages": [rich], "actor": "ops"},
        ),
        _post("conversation/read_turns", {"conversation_id": "wire-1", "after": 1}),
        writes=True,
    )
    session = {"conversation_id": "{session}"}
    paging = _serve(
        "conversation paging",
        _post("conversation/read_turns", {**session, "limit": 2}),
        _post("conversation/read_turns", {**session, "after": 2}),
        _post("conversation/read_turns", {**session, "limit": 0}),
        _post("conversation/read_turns", {**session, "after": -1}),
        _post("conversation/read_turns", {**session, "limit": -1}),
        _post("conversation/read_turns", {"conversation_id": "a/b"}),
        _post("conversation/read_projections", session),
        _post("conversation/read_projections", {**session, "after": 0, "limit": 5}),
        _post("conversation/read_turns", {**session, "after": None, "limit": None}),
    )
    search = "conversation/search_turns"
    searches = _serve(
        "search",
        _post(search, {"query": "turn"}),
        _post(search, {"query": "TURN", "conversations": ["{session}"], "limit": 1}),
        _post(search, {"query": "turn", "conversations": []}),
        _post(search, {"query": "user"}),
        _post(search, {"query": "turn", "limit": 501}),
        _post(search, {"query": "turn", "limit": 0}),
        _post(search, {"query": "   "}),
        _post(search, {"query": "turn", "conversations": ["a/b"]}),
        _post(search, {"query": "turn", "conversations": None, "limit": None}),
    )
    c = {"conversation_id": "wire-e"}
    redact, trail = "conversation/redact_turns", "conversation/turn_redactions"
    erasure = _serve(
        "erasure",
        _post("conversation/append_turn", {**c, "messages": [_HI]}),
        _post(
            "conversation/append_turn",
            {**c, "messages": [{"role": "user", "content": "the key is hunter2"}]},
        ),
        _post(
            "conversation/append_projections",
            {**c, "entries": [{"turn": 2, "kind": "digest", "text": "d", "span": 2}]},
        ),
        _post(redact, {**c, "turns": [2]}),
        _post("conversation/read_turns", c),
        _post("conversation/read_projections", c),
        _post(search, {"query": "hunter2"}),
        _post(trail, {"conversations": ["wire-e"]}),
        _post(redact, {**c, "actor": "ops"}),
        _post(trail, {"limit": 1}),
        _post(redact, {**c, "through": 0}),
        _post(redact, {**c, "through": 1, "turns": [1]}),
        _post(redact, {**c, "turns": [0]}),
        _post(redact, {"conversation_id": "a/b"}),
        _post(redact, {"conversation_id": "never"}),
        _post(trail, {"limit": 501}),
        _post(trail, {"limit": 0}),
        _post(trail, {"since": "2026-10-10T00:00:00"}),
        _post(trail, {"conversations": [], "since": None}),
        _post(redact, {**c, "through": None, "turns": None, "actor": None}),
        writes=True,
    )
    return [lifecycle, paging, searches, erasure]


def _store() -> Case:
    doc = {
        "scope": "user:r",
        "path": "notes/a",
        "content": "x",
        "version": 1,
        "created_at": _T0,
        "updated_at": _T0,
        "actor": "conv:x#1",
        "redacted": False,
        "extra": {"custom": "kept", "count": 3, "flag": True},
    }
    row = {
        "path": "notes/a",
        "version": 1,
        "action": "created",
        "content": "x",
        "actor": "conv:x#1",
        "created_at": _T0,
        "redacted": False,
    }
    act = {"path": "old", "actor": "conv:x#2", "created_at": _T0, "count": 1}
    turn = {
        "conversation_id": "r1",
        "turn": 1,
        "messages": [_HI],
        "created_at": _T0,
        "actor": None,
        "redacted": False,
    }
    skeleton = {
        **turn,
        "conversation_id": "r2",
        "messages": [],
        "actor": "conv:x",
        "redacted": True,
    }
    erased = {"conversation_id": "r2", "turns": [1], "actor": "ops", "created_at": _T0}
    empty: dict[str, Any] = {"documents": [], "versions": [], "redactions": []}
    return _serve(
        "store",
        _post("store/scopes"),
        _post("store/conversations"),
        _post(
            "store/restore_scope",
            {
                "scope": "user:r",
                "documents": [doc],
                "versions": [row],
                "redactions": [act],
            },
        ),
        _post("memory/read", {"scope": "user:r", "path": "notes/a"}),
        _post("memory/history", {"scope": "user:r"}),
        _post("memory/redactions", {"scope": "user:r"}),
        _post("memory/write", {"scope": "user:r", "path": "notes/a", "content": "y"}),
        _post("memory/read", {"scope": "user:r", "path": "notes/a"}),
        _post("store/restore_scope", {"scope": "user:r", **empty}),
        _post(
            "store/restore_conversation",
            {
                "conversation_id": "r1",
                "turns": [turn],
                "projections": [{"turn": 1, "kind": "digest", "text": "d", "span": 1}],
            },
        ),
        _post("conversation/read_turns", {"conversation_id": "r1"}),
        _post("conversation/read_projections", {"conversation_id": "r1"}),
        _post(
            "store/restore_conversation",
            {
                "conversation_id": "r2",
                "turns": [skeleton],
                "projections": [],
                "redactions": [erased],
            },
        ),
        _post("conversation/read_turns", {"conversation_id": "r2"}),
        _post("conversation/turn_redactions", {"conversations": ["r2"]}),
        _post(
            "store/restore_conversation",
            {"conversation_id": "r1", "turns": [], "projections": []},
        ),
        _post("store/scopes"),
        _post("store/conversations"),
        _post(
            "store/restore_scope",
            {
                "scope": "user:s",
                **empty,
                "documents": [{**_without(doc, "extra"), "scope": "user:s"}],
            },
        ),
        _post(
            "store/restore_scope",
            {"scope": "user:t", **empty, "documents": [_without(doc, "actor")]},
        ),
        _post(
            "store/restore_scope",
            {"scope": "user:t", **empty, "versions": [_without(row, "redacted")]},
        ),
        _post(
            "store/restore_conversation",
            {
                "conversation_id": "r3",
                "turns": [
                    {**_without(turn, "actor", "redacted"), "conversation_id": "r3"}
                ],
                "projections": [],
            },
        ),
        _post("conversation/read_turns", {"conversation_id": "r3"}),
        writes=True,
    )


def _envelopes() -> Case:
    w = {"scope": "user:e", "path": "notes/a", "content": "x"}
    append, project = "conversation/append_turn", "conversation/append_projections"
    e = {"conversation_id": "e1"}

    def message(body: Mapping[str, Any]) -> Exchange:
        return _post(append, {**e, "messages": [body]})

    def entry(body: Mapping[str, Any]) -> Exchange:
        return _post(project, {**e, "entries": [body]})

    return _serve(
        "envelopes",
        _post("memory/write", w),
        _post("memory/write", {**w, "expected_version": 5}),
        _post("memory/write", {**w, "path": "new", "expected_version": 1}),
        _post("memory/rename", {"scope": "user:e", "src": "notes/a", "dst": "notes/a"}),
        _post("memory/rename", {"scope": "user:e", "src": "nope", "dst": "x"}),
        _post("memory/read", {"scope": "bad", "path": "x"}),
        _post("memory/read", {"scope": "user:e", "path": "/bad"}),
        _post("memory/read", {"scope": "user:e"}),
        _post("memory/read", {"scope": "user:e", "path": 5}),
        _post("memory/write", {**w, "expected_version": True}),
        _post("memory/write", {**w, "expected_version": "1"}),
        _post("memory/versions", {"scope": "user:e", "path": "notes/a", "limit": "x"}),
        _post("memory/history", {"scope": "user:e", "since": 5}),
        _post("memory/history", {"scope": "user:e", "since": "yesterday"}),
        _post("memory/history", {"scope": "user:e", "since": "2026-10-10T00:00:00"}),
        message({"role": "robot"}),
        _post(append, {**e, "messages": []}),
        message({"content": "no role"}),
        message({"role": "user", "content": [{"type": "bogus"}]}),
        message(
            {
                "role": "assistant",
                "tool_calls": [{"id": "c1", "name": "m", "arguments": "{}"}],
            }
        ),
        message({"role": "assistant", "tool_calls": [{"id": 5, "name": "m"}]}),
        message({"role": "assistant", "tool_calls": [{"id": "c1"}]}),
        message({"role": "user", "content": [{"type": "text"}]}),
        message({"role": "user", "content": [{"type": "image", "data": "abc"}]}),
        _post(append, {**e, "messages": "x"}),
        _post(append, {**e, "messages": [5]}),
        message({"role": "user", "content": "hi", "name": "b", "extra": {}}),
        message({"role": "tool", "tool_call_id": ""}),
        message({"role": "assistant", "tool_calls": [{"id": "c2", "name": "m"}]}),
        entry({"turn": 1, "kind": "log", "text": "t", "span": None}),
        entry({"turn": 1, "kind": "log", "text": "t", "span": "1"}),
        entry({"turn": "1", "kind": "log", "text": "t"}),
        entry({"turn": 1, "kind": "log"}),
        entry({"turn": 1, "kind": "bogus", "text": "t"}),
        entry({"turn": 0, "kind": "log", "text": "t"}),
        entry({"turn": 1, "kind": "log", "text": "t", "span": 2}),
        _post("conversation/read_projections", e),
        _post("conversation/read_turns", e),
        writes=True,
    )


def _bodies() -> Case:
    return _serve(
        "body shapes",
        _raw("memory/read", b"[]"),
        _raw("memory/read", b"null"),
        _raw("memory/read", b'"x"'),
        _raw("memory/read", b""),
        _raw("memory/read", b"{"),
        _raw(
            "memory/read",
            b'{"scope": "user:a", "path": "x"}',
            **{"Content-Type": "text/plain"},
        ),
        _raw("store/scopes", b'{"scope": "user:zzz", "junk": 1}'),
        _post("memory/read", {"scope": "{proj}", "path": "facts", "junk": 1}),
    )


def _allowance() -> Case:
    table = "client:alice@{user}+alice-=alice-tok,client:ops=ops-tok"
    alice, ops = "alice-tok", "ops-tok"
    return _serve(
        "allowance",
        _get("/v1/capabilities", token=alice),
        _post("memory/read", {"scope": "{user}", "path": "profile"}, token=alice),
        _post("memory/read", {"scope": "user:bob", "path": "profile"}, token=alice),
        _post(
            "conversation/last_turn_number", {"conversation_id": "alice-1"}, token=alice
        ),
        _post(
            "conversation/last_turn_number", {"conversation_id": "bob-1"}, token=alice
        ),
        _post("conversation/search_turns", {"query": "turn"}, token=alice),
        _post(
            "conversation/search_turns",
            {"query": "turn", "conversations": ["alice-1", "bob-1"]},
            token=alice,
        ),
        _post("conversation/turn_redactions", {}, token=alice),
        _post("store/scopes", token=alice),
        Exchange("GET", "/mcp", None, alice),
        _post("store/scopes", token=ops),
        _post("memory/read", {"scope": "{user}", "path": "profile"}, token="wrong"),
        _post(
            "memory/write",
            {"scope": "{user}", "path": "notes/alice", "content": "mine", "actor": "a"},
            token=alice,
        ),
        env={"NEOSIAN_SERVE_TOKEN": table},
        writes=True,
    )


def _refusals() -> list[Case]:
    """The grammar tier (DESIGN §14.1): an invocation both doors refuse at
    exit 2 in one sentence, with nothing built and nothing bound."""
    t = TOKEN_ENV
    at = ("serve", "--root", "{home}")

    def refused(name: str, *argv: str, env: Mapping[str, str] = t) -> Case:
        return Case(f"serve {name} refused", argv, env=env)

    return [
        refused("port", *at, "--port", "0"),
        refused("port high", *at, "--port", "70000"),
        refused("schema", *at, "--schema", "x", "--port", "1"),
        refused("url without token", "serve", "--url", "http://x", "--port", "1"),
        refused(
            "url",
            "serve",
            "--url",
            "http://x",
            "--port",
            "1",
            env={**t, "NEOSIAN_CLIENT_TOKEN": "c"},
        ),
        refused("url shape", "serve", "--url", "x", "--port", "1"),
        refused("root and url", *at, "--url", "http://x", "--port", "1"),
        refused("token required", *at, "--port", "1", env={}),
        refused("table", *at, "--port", "1", env={"NEOSIAN_SERVE_TOKEN": "a="}),
        refused(
            "table actor", *at, "--port", "1", env={"NEOSIAN_SERVE_TOKEN": "Nope=t"}
        ),
        refused(
            "table prefix",
            *at,
            "--port",
            "1",
            env={"NEOSIAN_SERVE_TOKEN": "client:a@=t"},
        ),
        refused("actor", *at, "--actor", "Nope", "--port", "1"),
        refused(
            "sugar with mounts",
            *at,
            "--scope",
            "{user}",
            "--mount",
            "scope={proj},path=p",
            "--port",
            "1",
        ),
        refused("mount token", *at, "--mount", "scope={user}", "--port", "1"),
        refused(
            "mounts collide",
            *at,
            "--mount",
            "scope={user},path=x",
            "--mount",
            "scope={proj},path=x",
            "--port",
            "1",
        ),
        refused("scope", *at, "--mount", "scope=nobody,path=user", "--port", "1"),
    ]


def _rpc(
    id_: int, method: str, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        message["params"] = params
    return message


def _call(id_: int, tool: str, **arguments: Any) -> dict[str, Any]:
    return _rpc(id_, "tools/call", {"name": tool, "arguments": arguments})


def _mcp(
    body: Mapping[str, Any] | bytes | None,
    session: int | None = None,
    *,
    method: str = "POST",
    token: str | None = TOKEN,
    headers: Mapping[str, str] | None = None,
) -> Exchange:
    """One `/mcp` exchange: the SDK client's two headers unless overridden,
    the session the N-th answer minted when `session` names it."""
    carried = {**_MCP_HEADERS, **(headers or {})}
    if session is not None:
        carried[SESSION_HEADER] = f"{{session:{session}}}"
    return Exchange(method, "/mcp", body, token, carried)


def _mcp_session() -> Case:
    """One session: the handshake, the state set, the refusals inside it,
    DELETE and what follows."""
    return _serve(
        "mcp session",
        _mcp(_INITIALIZE),
        _mcp(_INITIALIZED, 0),
        _mcp(_rpc(2, "tools/list"), 0),
        _mcp(_rpc(3, "ping"), 0),
        _mcp(_rpc(4, "prompts/list"), 0),
        _mcp(_rpc(5, "prompts/get", {"name": "deploy"}), 0),
        _mcp(_rpc(6, "prompts/get", {"name": "nope"}), 0),
        _mcp(_rpc(7, "resources/list"), 0),
        _mcp(_call(8, "memory", command="view", path="/"), 0),
        _mcp(_call(9, "search_history", query="turn"), 0),
        _mcp(_call(10, "recall_turn", turn=2, conversation="{session}"), 0),
        _mcp(_call(11, "nope"), 0),
        _mcp(_rpc(12, "tools/call", {"name": "memory"}), 0),
        _mcp(_INITIALIZE, 0),
        _mcp({"jsonrpc": "2.0", "id": 77, "result": {}}, 0),
        _mcp({"jsonrpc": "2.0", "id": 1.5, "method": "ping"}, 0),
        _mcp(_rpc(13, "ping"), 0, headers={"Accept": "application/json"}),
        _mcp(_rpc(13, "ping"), 0, headers={"Content-Type": "text/plain"}),
        _mcp(b"{nope", 0),
        _mcp({}, 0, method="PUT"),
        _mcp(None, 0, method="GET", headers={"Accept": "application/json"}),
        _mcp(None, 0, method="DELETE"),
        _mcp(_rpc(14, "ping"), 0),
        _mcp(None, 0, method="DELETE"),
    )


def _mcp_without_session() -> Case:
    """The requests that present no session: only `initialize` opens one,
    and an opening request refused leaves nothing behind."""
    return _serve(
        "mcp without a session",
        _mcp(_rpc(1, "ping")),
        _mcp(_rpc(1, "ping"), headers={SESSION_HEADER: "nope"}),
        _mcp(None, method="GET"),
        _mcp(None, method="DELETE"),
        _mcp(_INITIALIZED),
        _mcp(_INITIALIZE, headers={"Accept": "text/plain"}),
        _mcp(_INITIALIZE),
        _mcp(_rpc(2, "tools/list"), 6),
    )


def _mcp_writes() -> Case:
    """A session's writes as the process-wide actor, read back through the
    same process's store routes."""
    document = {"scope": "{proj}", "path": _OVER_MCP}
    return _serve(
        "mcp writes",
        _mcp(_INITIALIZE),
        _mcp(_INITIALIZED, 0),
        _mcp(
            _call(
                2,
                "memory",
                command="create",
                path=f"/project/{_OVER_MCP}",
                content="# Over MCP\n\nWritten through /mcp.\n",
            ),
            0,
        ),
        _mcp(
            _call(
                3,
                "memory",
                command="str_replace",
                path=f"/project/{_OVER_MCP}",
                old_str="Written",
                new_str="Rewritten",
            ),
            0,
        ),
        _mcp(_call(4, "memory", command="view", path=f"/project/{_OVER_MCP}"), 0),
        _mcp(_call(5, "handoff", note="The wire departs; pick up the release."), 0),
        _post("memory/read", document),
        _post("memory/versions", document),
        writes=True,
    )


def _mcp_read_back() -> Case:
    """The wire's reads of what a session wrote (test_mixed_home)."""
    document = {"scope": "{proj}", "path": _OVER_MCP}
    return _serve(
        "mcp read back",
        _post("memory/read", document),
        _post("memory/versions", document),
    )


def serve_cases() -> list[Case]:
    return [
        _handshake(),
        *_memory(),
        *_conversation(),
        _store(),
        _envelopes(),
        _bodies(),
        _allowance(),
        *_refusals(),
        _mcp_session(),
        _mcp_without_session(),
        _mcp_writes(),
        _mcp_read_back(),
    ]
