"""The corpus (DESIGN §39.4): every case the replay holds against both
doors over the seeded home. The hook payloads per client are the
documented shapes the unit tier replays (the same builders); the memory
grammar with the store's rulings, the operator verbs and one MCP session
follow. A case asserts only once its verb is in `harness.BINARY_VERBS`."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

from tests.differential.harness import Case
from tests.unit.record.payloads import prompt, session_start, stop, tool

# The corpus's own sessions: new conversations beside the seed's.
SESSION: Final = "7c1e2b3a-4d5f-4a6b-8c9d-0e1f2a3b4c5d"
CURSOR_SESSION: Final = "cursor-corpus"
IDS: Final[frozenset[str]] = frozenset({SESSION})
RECORD: Final = (
    "record",
    "--root",
    "{home}",
    "--spool",
    "{spool}",
    "--project",
    "{project}",
)
MOUNTS: Final = (
    "--root",
    "{home}",
    "--mount",
    "scope={user},path=user",
    "--mount",
    "scope={proj},path=project",
)
ROOT: Final = ("--root", "{home}")
# The user's mount read-only, the project's edit-only.
POLICIES: Final = (
    *ROOT,
    "--mount",
    "scope={user},path=user,ro",
    "--mount",
    "scope={proj},path=project,eo",
)
_CLIENTS: Final = ("codex", "muse-code", "pi", "opencode")


def _cursor(event: str, **fields: Any) -> dict[str, Any]:
    return {
        "conversation_id": CURSOR_SESSION,
        "generation_id": "generation-1",
        "cursor_version": "2026.09.10-fd3934a",
        "hook_event_name": event,
        "workspace_roots": ["{project}"],
        **fields,
    }


def _rpc(
    id_: int, method: str, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        message["params"] = params
    return message


def _memory(
    name: str,
    *argv: str,
    mounts: tuple[str, ...] = MOUNTS,
    text: bool = False,
    stdin: str | None = None,
    writes: bool = True,
) -> Case:
    """One memory invocation: the envelope under `--json` unless `text`."""
    tail = () if text else ("--json",)
    return Case(
        f"memory {name}", ("memory", *argv, *mounts, *tail), stdin=stdin, writes=writes
    )


def _rulings() -> list[Case]:
    """The write rulings of the store and its tool layer (DESIGN §8), each
    over the seeded home: what a conformance kit pins on a store, as a
    shell observes it."""
    create, replace = "create", "str_replace"
    return [
        _memory(
            "create overwrite",
            create,
            "/project/facts",
            "--content",
            "# Facts\n\n- rewritten\n",
            text=True,
        ),
        _memory(
            "create from stdin",
            create,
            "/project/piped",
            "--content",
            "-",
            stdin="# Piped\n\nfrom a pipe\n",
        ),
        _memory(
            "create unicode",
            create,
            "/user/notes/unicode",
            "--content",
            'café ✓ 日本 — "q" \\ \ttab\n',
        ),
        _memory(
            "create over a deletion",
            create,
            "/project/scratch",
            "--content",
            "# Scratch\n\nback again\n",
        ),
        _memory(
            "create mailbox refused", create, "/project/messages/x", "--content", "x"
        ),
        _memory(
            "create mount root refused", create, "/project", "--content", "x", text=True
        ),
        _memory(
            "create read-only refused",
            create,
            "/user/x",
            "--content",
            "x",
            mounts=POLICIES,
        ),
        _memory(
            "create edit-only refused",
            create,
            "/project/brand-new",
            "--content",
            "x",
            mounts=POLICIES,
            text=True,
        ),
        _memory(
            "create edit-only overwrite",
            create,
            "/project/facts",
            "--content",
            "# Facts\n\nedited in place\n",
            mounts=POLICIES,
        ),
        _memory(
            "str_replace absent",
            replace,
            "/project/facts",
            "--old-str",
            "absent",
            "--new-str",
            "x",
        ),
        _memory(
            "str_replace ambiguous",
            replace,
            "/project/facts",
            "--old-str",
            "e",
            "--new-str",
            "x",
            text=True,
        ),
        _memory(
            "str_replace redacted",
            replace,
            "/project/secret",
            "--old-str",
            "a",
            "--new-str",
            "x",
        ),
        _memory(
            "insert out of range",
            "insert",
            "/project/decisions",
            "--insert-line",
            "99",
            "--insert-text",
            "x",
        ),
        _memory("delete missing", "delete", "/project/missing", text=True),
        _memory(
            "delete edit-only refused", "delete", "/project/facts", mounts=POLICIES
        ),
        _memory("rename occupied", "rename", "/project/facts", "/project/decisions"),
        _memory(
            "rename missing", "rename", "/project/missing", "/project/other", text=True
        ),
        _memory("rename across mounts", "rename", "/project/facts", "/user/facts"),
        _memory("redact text", "redact", "/project/decisions", text=True),
        _memory("redact scope", "redact", "/user", "--all"),
        _memory("redact deleted path", "redact", "/project/scratch"),
        _memory("redact read-only refused", "redact", "/user/profile", mounts=POLICIES),
        _memory("revert stale", "revert", "/user/profile", "--version", "2"),
        _memory("revert a creation", "revert", "/user/preferences", "--version", "1"),
        _memory(
            "revert redacted", "revert", "/project/secret", "--version", "1", text=True
        ),
    ]


def _refusals() -> list[Case]:
    """The grammar tier (DESIGN §14.1): an invocation both doors refuse at
    exit 2 in one sentence, with nothing built."""
    collide = (
        *ROOT,
        "--mount",
        "scope={user},path=x",
        "--mount",
        "scope={proj},path=x",
    )
    return [
        _memory(
            "versions limit refused",
            "versions",
            "/project/facts",
            "--limit",
            "0",
            text=True,
            writes=False,
        ),
        _memory("redact mount refused", "redact", "/project", writes=False),
        _memory(
            "redact all on a document refused",
            "redact",
            "/project/facts",
            "--all",
            text=True,
            writes=False,
        ),
        _memory(
            "revert version refused",
            "revert",
            "/project/facts",
            "--version",
            "0",
            writes=False,
        ),
        _memory(
            "actor refused", "view", "/", "--actor", "Nope", text=True, writes=False
        ),
        _memory(
            "sugar with mounts refused", "view", "/", "--scope", "{user}", writes=False
        ),
        _memory(
            "mount token refused",
            "view",
            "/",
            mounts=(*ROOT, "--mount", "scope={user}"),
            text=True,
            writes=False,
        ),
        _memory("mounts collide", "view", "/", mounts=collide, writes=False),
        Case("search limit refused", ("search", "x", "--limit", "0", *ROOT)),
        Case(
            "search limit refused json",
            ("search", "x", "--limit", "501", *ROOT, "--json"),
        ),
        Case("search blank refused", ("search", " ", *ROOT)),
        Case(
            "search conversation refused json",
            ("search", "x", "--conversation", "a/b", *ROOT, "--json"),
        ),
        Case("continue id refused", ("continue", "a/b", *ROOT, "--scope", "{proj}")),
        Case(
            "continue scope refused json",
            ("continue", *ROOT, "--scope", "bad", "--json"),
        ),
    ]


def _cli() -> list[Case]:
    docs = [
        Case("docs listing", ("docs",)),
        Case("docs listing json", ("docs", "--json")),
        Case("docs page", ("docs", "cli")),
        Case("docs page json", ("docs", "cli", "--json")),
        Case("docs quickstart", ("docs", "quickstart")),
        Case("docs unknown", ("docs", "nope")),
        Case("docs unknown json", ("docs", "nope", "--json")),
    ]
    memory = [
        Case("memory index", ("memory", "view", "/", *MOUNTS)),
        Case("memory index json", ("memory", "view", "/", *MOUNTS, "--json")),
        Case("memory view document", ("memory", "view", "/project/facts", *MOUNTS)),
        Case(
            "memory view skill json",
            ("memory", "view", "/project/skills/release", *MOUNTS, "--json"),
        ),
        Case("memory view directory", ("memory", "view", "/user", *MOUNTS)),
        Case(
            "memory view nested directory",
            ("memory", "view", "/project/skills", *MOUNTS),
        ),
        Case(
            "memory view mailbox directory",
            ("memory", "view", "/project/messages/", *MOUNTS),
        ),
        Case(
            "memory view range",
            (
                "memory",
                "view",
                "/project/skills/release",
                "--view-range",
                "5",
                "-1",
                *MOUNTS,
            ),
        ),
        Case(
            "memory view range refused",
            ("memory", "view", "/project/facts", "--view-range", "40", "41", *MOUNTS),
        ),
        Case("memory view redacted", ("memory", "view", "/project/secret", *MOUNTS)),
        Case("memory view missing", ("memory", "view", "/project/missing", *MOUNTS)),
        Case(
            "memory view unknown mount json",
            ("memory", "view", "/elsewhere/facts", *MOUNTS, "--json"),
        ),
        Case(
            "memory index past the budget",
            (
                "memory",
                "view",
                "/",
                *ROOT,
                "--mount",
                "scope={user},path=user",
                "--mount",
                "scope={user}/proj:archive,path=archive",
            ),
        ),
        Case("memory index sugar", ("memory", "view", "/", *ROOT, "--scope", "{proj}")),
        Case("memory index layout", ("memory", "view", "/", *ROOT)),
        Case("memory index policies", ("memory", "view", "/", *POLICIES)),
        Case(
            "memory scope refused",
            ("memory", "view", "/", *ROOT, "--mount", "scope=nobody,path=user"),
        ),
        Case(
            "memory create",
            (
                "memory",
                "create",
                "/project/notes/corpus",
                "--content",
                "# Corpus\n\nwritten by the replay\n",
                *MOUNTS,
                "--json",
            ),
            writes=True,
        ),
        Case(
            "memory str_replace",
            (
                "memory",
                "str_replace",
                "/project/facts",
                "--old-str",
                "eu-west-1 (v1)",
                "--new-str",
                "eu-west-2 (v2)",
                *MOUNTS,
                "--json",
            ),
            writes=True,
        ),
        Case(
            "memory insert",
            (
                "memory",
                "insert",
                "/project/decisions",
                "--insert-line",
                "3",
                "--insert-text",
                "- 2026-10-10: the binary\n",
                *MOUNTS,
                "--json",
            ),
            writes=True,
        ),
        Case(
            "memory delete",
            ("memory", "delete", "/project/new-name", *MOUNTS, "--json"),
            writes=True,
        ),
        Case(
            "memory rename",
            (
                "memory",
                "rename",
                "/project/decisions",
                "/project/choices",
                *MOUNTS,
                "--json",
            ),
            writes=True,
        ),
        Case(
            "memory versions json",
            ("memory", "versions", "/project/facts", *MOUNTS, "--json"),
        ),
        Case("memory versions", ("memory", "versions", "/user/profile", *MOUNTS)),
        Case(
            "memory versions limit json",
            ("memory", "versions", "/user/profile", "--limit", "2", *MOUNTS, "--json"),
        ),
        Case(
            "memory versions deleted json",
            ("memory", "versions", "/project/scratch", *MOUNTS, "--json"),
        ),
        Case(
            "memory versions redacted",
            ("memory", "versions", "/project/secret", *MOUNTS),
        ),
        Case(
            "memory versions never written",
            ("memory", "versions", "/project/never", *MOUNTS),
        ),
        Case(
            "memory versions mount root json",
            ("memory", "versions", "/project", *MOUNTS, "--json"),
        ),
        Case(
            "memory redact",
            ("memory", "redact", "/project/facts", *MOUNTS, "--json"),
            writes=True,
        ),
        Case(
            "memory revert",
            ("memory", "revert", "/user/profile", "--version", "3", *MOUNTS, "--json"),
            writes=True,
        ),
    ]
    operator = [
        Case(
            "audit scope json",
            ("audit", "--scope", "{proj}", "--root", "{home}", "--json"),
        ),
        Case("search json", ("search", "turn", "--root", "{home}", "--json")),
        Case("search", ("search", "turn", *ROOT)),
        Case(
            "search narrowed json",
            ("search", "eu-west-1", "--conversation", "{session}", *ROOT, "--json"),
        ),
        Case("search two terms limit", ("search", "the", "ttl", "--limit", "2", *ROOT)),
        Case(
            "search a duplicate conversation",
            (
                "search",
                "the",
                "--conversation",
                "{session}",
                "--conversation",
                "{session}",
                *ROOT,
                "--limit",
                "4",
            ),
        ),
        Case("search none", ("search", "zzz", *ROOT)),
        Case("search none json", ("search", "zzz", "turn", *ROOT, "--json")),
        Case("search message", ("search", "upstream", *ROOT)),
        Case("search message json", ("search", "upstream", *ROOT, "--json")),
        Case(
            "messages list json",
            ("messages", "list", "--root", "{home}", "--scope", "{proj}", "--json"),
        ),
        Case(
            "continue json",
            ("continue", "--root", "{home}", "--scope", "{proj}", "--json"),
        ),
        Case("continue", ("continue", *ROOT, "--scope", "{proj}")),
        Case("continue the layout's scope", ("continue", *ROOT)),
        Case("continue named", ("continue", "{session}", *ROOT, "--scope", "{proj}")),
        Case(
            "continue named json",
            ("continue", "{session}", *ROOT, "--scope", "{proj}", "--json"),
        ),
        Case("continue a stranger", ("continue", "nope", *ROOT, "--scope", "{proj}")),
        Case(
            "continue an empty scope json",
            ("continue", *ROOT, "--scope", "user:nobody", "--json"),
        ),
        Case("status json", ("status", "--json")),
        Case(
            "prune dry run json",
            ("prune", "--older-than", "1d", "--dry-run", "--root", "{home}", "--json"),
        ),
        Case(
            "export json",
            ("export", "{out}", "--root", "{home}", "--json"),
            writes=True,
        ),
    ]
    version = Case("version", ("version",), token=re.compile(r"v[0-9][0-9A-Za-z.]*"))
    return [version, *docs, *memory, *_rulings(), *_refusals(), *operator]


def _hooks() -> list[Case]:
    claude = [
        Case(
            "claude-code session start",
            RECORD,
            payload=session_start("startup", session=SESSION),
        ),
        Case(
            "claude-code session compact",
            RECORD,
            payload=session_start("compact", session=SESSION),
        ),
        Case(
            "claude-code session resume",
            RECORD,
            payload=session_start("resume", session=SESSION),
        ),
        Case(
            "claude-code prompt",
            RECORD,
            payload=prompt("Add a test for login", session=SESSION),
            writes=True,
        ),
        Case("claude-code tool", RECORD, payload=tool(session=SESSION), writes=True),
        Case(
            "claude-code subagent tool",
            RECORD,
            payload=tool(session=SESSION, agent_id="sub-1"),
            writes=True,
        ),
        Case(
            "claude-code stop alone", RECORD, payload=stop(session=SESSION), writes=True
        ),
    ]
    others = [
        Case(
            f"{client} {event}",
            (*RECORD, "--agent", client),
            payload=payload,
            writes=event != "session start",
        )
        for client in _CLIENTS
        for event, payload in (
            ("session start", session_start("startup", session=SESSION)),
            ("prompt", prompt(f"{client} asks", session=SESSION)),
            ("tool", tool(session=SESSION)),
            ("stop alone", stop(session=SESSION)),
        )
        if not (client == "opencode" and event == "session start")
    ]
    cursor = [
        Case(
            "cursor session start",
            (*RECORD, "--agent", "cursor"),
            payload=_cursor("sessionStart"),
        ),
        Case(
            "cursor prompt",
            (*RECORD, "--agent", "cursor"),
            payload=_cursor("beforeSubmitPrompt", prompt="hello"),
            writes=True,
        ),
        Case(
            "cursor tool",
            (*RECORD, "--agent", "cursor"),
            payload=_cursor(
                "postToolUse",
                tool_name="Shell",
                tool_use_id="call-1",
                tool_input={"command": "pwd"},
                tool_output="/work",
            ),
            writes=True,
        ),
        Case(
            "cursor response",
            (*RECORD, "--agent", "cursor"),
            payload=_cursor("afterAgentResponse", text="done"),
            writes=True,
        ),
        Case(
            "cursor stop",
            (*RECORD, "--agent", "cursor"),
            payload=_cursor("stop", status="completed"),
            writes=True,
        ),
    ]
    handoff = tool(
        "mcp__neosian-memory__handoff",
        tool_input={"note": "Take over the release: the tag is not cut."},
        response={
            "type": "text",
            "text": "[handoff note recorded — the next session in this scope sees it "
            "at start, and continue_session() there delivers this conversation]",
        },
        tool_use_id="toolu_handoff",
        session=SESSION,
    )
    diagnostics = [
        Case(
            f"{name} json",
            (*RECORD, "--json"),
            payload=payload,
            writes=name != "claude-code session start",
        )
        for name, payload in (
            ("claude-code session start", session_start("startup", session=SESSION)),
            ("claude-code prompt", prompt("Add a test for login", session=SESSION)),
            ("claude-code tool", tool(session=SESSION)),
            ("claude-code subagent tool", tool(session=SESSION, agent_id="sub-1")),
            ("claude-code stop alone", stop(session=SESSION)),
        )
    ]
    more = [
        Case(
            "claude-code stop empty",
            RECORD,
            payload=stop("", session=SESSION),
            writes=True,
        ),
        Case("claude-code handoff tool", RECORD, payload=handoff, writes=True),
        Case(
            "cursor prompt without a project",
            ("record", "--root", "{home}", "--spool", "{spool}", "--agent", "cursor"),
            payload=_cursor("beforeSubmitPrompt", prompt="hello", workspace_roots=[]),
            writes=True,
        ),
    ]
    return [*claude, *others, *cursor, *diagnostics, *more]


def _mailbox() -> list[Case]:
    """The plugins' write events (DESIGN §34): Pi's poll with no wake
    pending, and OpenCode's delivery report on a message the home does
    not hold (its id canonical, so the refusal is the store's)."""
    plugin = (
        "record",
        "--root",
        "{home}",
        "--spool",
        "{spool}",
        "--project",
        "{project}",
    )
    poll = {"session_id": SESSION, "hook_event_name": "MailboxPoll"}
    delivery = {
        "session_id": SESSION,
        "hook_event_name": "MailboxDelivery",
        "scope": "{proj}",
        "message_id": "00000000-0000-4000-8000-000000000000",
        "occurrence": 1,
        "token": "stale",
        "accepted": True,
    }
    return [
        Case("pi mailbox poll", (*plugin, "--agent", "pi", "--json"), payload=poll),
        Case(
            "opencode mailbox delivery unknown",
            (*plugin, "--agent", "opencode", "--json"),
            payload=delivery,
        ),
    ]


def _mcp() -> list[Case]:
    initialize = _rpc(
        1,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "differential", "version": "0"},
        },
    )
    initialized = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    reads = (
        initialize,
        initialized,
        _rpc(2, "tools/list"),
        _rpc(3, "prompts/list"),
        _rpc(
            4,
            "tools/call",
            {"name": "memory", "arguments": {"command": "view", "path": "/"}},
        ),
        _rpc(
            5, "tools/call", {"name": "search_history", "arguments": {"query": "turn"}}
        ),
        _rpc(6, "tools/call", {"name": "continue_session", "arguments": {}}),
    )
    writes = (
        initialize,
        initialized,
        _rpc(
            2,
            "tools/call",
            {
                "name": "handoff",
                "arguments": {"note": "The replay departs; pick up the release."},
            },
        ),
    )

    def call(id_: int, tool: str, **arguments: Any) -> dict[str, Any]:
        return _rpc(id_, "tools/call", {"name": tool, "arguments": arguments})

    skills = (
        initialize,
        initialized,
        call(2, "list_skills"),
        call(3, "load_skill", name="deploy"),
        call(4, "load_skill", name="/project/skills/release"),
        call(5, "load_skill", name="handoff"),
        call(6, "load_skill", name="nope"),
        _rpc(7, "prompts/get", {"name": "deploy"}),
        _rpc(8, "prompts/get", {"name": "nope"}),
    )
    recalls = (
        initialize,
        initialized,
        call(2, "recall_turn", turn=2, conversation="{session}"),
        call(3, "recall_turn", turn="1", conversation="{session}", call="toolu_0200"),
        call(4, "recall_turn", turn=9, conversation="{session}"),
        call(5, "recall_turn", turn=1, conversation="{session}", call="nope"),
        call(6, "recall_turn", turn=1),
        call(7, "search_history", query="eu-west-1", conversation="{session}"),
        call(8, "search_history", query="   "),
        call(9, "search_history", query="nothing-holds-this"),
        call(10, "memory", command="view", path="/project/facts", view_range=[1, 2]),
        call(11, "memory", command="view", path="/project/facts", view_range=[1]),
        call(12, "memory", command="create", path="/project/messages/x", content="no"),
        call(13, "memory", command="insert", path="/project/facts"),
        call(14, "messages", command="list", status="all"),
        call(15, "messages", command="view", scope="/project", message_id="x"),
        call(16, "nope"),
        _rpc(17, "tools/call", {"name": "memory"}),
        _rpc(18, "ping"),
        _rpc(19, "resources/list"),
    )
    mailbox = (
        initialize,
        initialized,
        call(
            2,
            "messages",
            command="send",
            scope="/project",
            body="A note for later",
            actionable=True,
        ),
        call(
            3,
            "messages",
            command="send",
            scope="/user",
            body="Wake me",
            delivery="wake",
            session=SESSION,
        ),
        call(4, "messages", command="send", scope="/project", body="", session=SESSION),
        call(
            5,
            "messages",
            command="send",
            scope="/project",
            body="Due",
            due_at="2026-01-01T00:00:00",
        ),
        call(6, "messages", command="list", status="all", limit=2),
        call(
            7,
            "messages",
            command="ack",
            scope="/project",
            message_id="00000000-0000-4000-8000-000000000000",
            occurrence=1,
            session=SESSION,
        ),
        call(
            8,
            "memory",
            command="create",
            path="/project/notes/after",
            content="after the mailbox",
        ),
    )
    pre_initialize = (
        _rpc(1, "tools/list"),
        initialize,
        _rpc(2, "tools/list"),
    )
    return [
        Case("mcp reads", ("mcp", *MOUNTS), rpc=reads),
        Case("mcp handoff", ("mcp", *MOUNTS), rpc=writes, writes=True),
        Case("mcp skills", ("mcp", *MOUNTS), rpc=skills),
        Case("mcp recalls", ("mcp", *MOUNTS), rpc=recalls),
        Case("mcp mailbox", ("mcp", *MOUNTS), rpc=mailbox, writes=True),
        Case("mcp before initialize", ("mcp", *MOUNTS), rpc=pre_initialize),
        Case(
            "mcp hooked client",
            ("mcp", *MOUNTS, "--actor", "mcp:claude-code"),
            rpc=(initialize,),
        ),
        Case(
            "mcp continue named",
            ("mcp", *MOUNTS),
            rpc=(
                initialize,
                initialized,
                call(2, "continue_session", conversation="{session}", session=SESSION),
            ),
            writes=True,
        ),
        Case(
            "mcp policies",
            ("mcp", *POLICIES),
            rpc=(
                initialize,
                initialized,
                _rpc(2, "tools/list"),
                call(3, "list_skills"),
                call(4, "handoff", note="x"),
            ),
        ),
    ]


_BATON: Final = (
    "# handoff from cli:local\n\n- from: cli:local\n- written: 2026-10-12T22:31:00Z\n"
    "- status: pending\n\nTake over the release: the tag is not cut.\n"
)


def _batons() -> list[Case]:
    """The baton by hand (DESIGN §33): a pending, unlinked note rewritten
    through the memory tool, and the baton deleted."""
    return [
        _memory("create baton", "create", "/project/handoff", "--content", _BATON),
        _memory("delete baton", "delete", "/project/handoff"),
    ]


CASES: Final[tuple[Case, ...]] = (*_cli(), *_batons(), *_hooks(), *_mailbox(), *_mcp())
BY_NAME: Final[dict[str, Case]] = {case.name: case for case in CASES}
# One document through every mutation, each step building on the one
# before: the script the two doors take turns on (test_mixed_home).
_SHARED: Final = "/project/shared"
_MOVED: Final = "/project/moved"
TURNS: Final[tuple[Case, ...]] = (
    _memory("turn create", "create", _SHARED, "--content", "# Shared\n\none\n"),
    _memory(
        "turn replace", "str_replace", _SHARED, "--old-str", "one", "--new-str", "two"
    ),
    _memory(
        "turn insert",
        "insert",
        _SHARED,
        "--insert-line",
        "1",
        "--insert-text",
        "inserted",
    ),
    _memory("turn overwrite", "create", _SHARED, "--content", "# Shared\n\ncafé ✓\n"),
    _memory("turn rename", "rename", _SHARED, _MOVED),
    _memory("turn undo", "revert", _MOVED, "--version", "1"),
    _memory("turn recreate", "create", _MOVED, "--content", "# Moved\n\nback\n"),
    _memory("turn redact", "redact", _MOVED),
    _memory("turn versions", "versions", _MOVED, writes=False),
    _memory("turn source versions", "versions", _SHARED, writes=False),
    _memory("turn index", "view", "/", text=True, writes=False),
)
