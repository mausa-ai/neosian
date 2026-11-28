"""A stand-in for the claude, codex and opencode headless commands.

Built from the real runs in record/x2b-captures-2026-11-21 (Claude Code
2.1.330, codex-cli 0.169.0, OpenCode 2.3.2): the same argv forms, the same
JSON events on stdout, the prompt read from stdin. It logs what it was
given to $STANDIN_DIR/log.jsonl. Words in the prompt choose a behavior:
`sleep:N`, `big:N`, `fail`, `huge-fail`, `deny`, `ignore-term`, `grandchild`,
`detached` (a process in a session and group of its own, as Codex starts
commands), `leave` (a process left in the group when the harness exits),
`hold` (a process with no environment, in its own session, holding stdout),
`imitate` (text shaped like neosian's observations), `forge` (announce the
session named by $STANDIN_FORGE_ID), `bad-session`, `record`, `record-noenv`, `probe-ro` (start the neosian server a read-only
child is given and try a read and a write), `running:N` (Codex: a command
started, as `item.started`, that completes N seconds later), `big-answer:N`
(the hooks run without the parent variables, as when a harness does not pass
its environment to hooks)
(run the neosian record hooks for the turn) and `probe` (start the child's
own neosian MCP server as the harness would and list its tools; Codex's
stand-in gives that server only the variables real Codex passes).
For the policy (X4b): `writes` (benign writes to the paths in
$STANDIN_WRITES, each answered `written` or with its error's name).
For the watcher's record (X4a): `tool:N` (one completed tool call whose
result holds N bytes, in each harness's own shape), `twice` (the tool call
and the completion printed twice), `record-bare` (the hooks run without any
of the harness variables, as an older hook binary or a harness that clears
its hooks' environment would) and `record-refused` (the hooks run where they
cannot write the spool or the store, as under a sandbox).
For sign-in and transports (X4c): `claude auth status` and `codex login
status` answer as Claude Code 2.1.332 and codex-cli 0.172.0 do, chosen by
$STANDIN_AUTH (`in`, `key`, `out`, `slow`, `malformed`, `error`, `network`);
`signin-fail` (the turn fails mid-way with each client's own sign-in
error); $STANDIN_CODEX_ENTRIES (neosian's Codex keys and their transports,
as a JSON object) and pairs in $STANDIN_CODEX_SERVERS (another server with a
transport), with Codex's refusal of a field its entry's transport does not
take ("env is not supported for streamable_http").
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

CODEX_PASSED = (
    "HOME",
    "LOGNAME",
    "PATH",
    "SHELL",
    "USER",
    "LANG",
    "LC_ALL",
    "TERM",
    "TMPDIR",
    "TZ",
    "__CF_USER_TEXT_ENCODING",
)


def emit(event: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def log(entry: dict[str, Any]) -> None:
    """One line of the log: through the test's collector when it listens
    (a stand-in under the macOS policy cannot write the test's directory),
    else appended directly."""
    address = os.environ.get("STANDIN_LOG")
    if address:
        host, port = address.rsplit(":", 1)
        line = {"dir": os.environ["STANDIN_DIR"], "entry": entry}
        with socket.create_connection((host, int(port)), timeout=10) as connection:
            connection.sendall((json.dumps(line) + "\n").encode())
            connection.makefile("rb").readline()
        return
    with (Path(os.environ["STANDIN_DIR"]) / "log.jsonl").open("a") as handle:
        handle.write(json.dumps(entry) + "\n")


def flag(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


def overrides(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, word in enumerate(argv[:-1]) if word == "-c"]


def codex_env(argv: list[str], key: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in CODEX_PASSED}
    for override in overrides(argv):
        found = re.fullmatch(r'mcp_servers\.([^.]+)\.env\.([A-Z_]+)="(.*)"', override)
        if found and found.group(1) == key:
            env[found.group(2)] = found.group(3)
    return env


def child_tools(env: dict[str, str], cwd: str) -> list[str]:
    """What the child's own neosian MCP server lists, started as the harness would."""
    answers = serve([os.environ["STANDIN_NEOSIAN"], "mcp"], env, cwd, [])
    return [tool["name"] for tool in answers[1]["result"]["tools"]]


def serve(
    command: list[str], env: dict[str, str], cwd: str, calls: list[dict[str, Any]]
) -> list[Any]:
    """Speak MCP to one server: initialize, list its tools, then `calls`."""
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ] + [
        {"jsonrpc": "2.0", "id": 3 + n, "method": "tools/call", "params": call}
        for n, call in enumerate(calls)
    ]
    done = subprocess.run(
        command,
        input="".join(json.dumps(line) + "\n" for line in lines),
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        timeout=30,
        check=False,
    )
    return [json.loads(line) for line in done.stdout.splitlines()]


def read_only_server(program: str, argv: list[str], cwd: str) -> dict[str, Any]:
    """The neosian server a read-only child gets, as the harness would start
    it: Claude's strict configuration, or Codex's registered entry with the
    run's overrides. Tries a read and a write."""
    if program == "claude":
        config = json.loads(argv[argv.index("--mcp-config") + 1])
        entry = config["mcpServers"]["neosian"]
        command = [entry["command"], *entry["args"]]
        env = dict(os.environ)
    else:
        # The registered entry ($STANDIN_CODEX_COMMAND, as a wrapper, or the
        # binary itself) with the run's `command` and `args` overrides.
        key = os.environ.get("STANDIN_CODEX_KEY", "neosian-memory")
        program_path = os.environ.get(
            "STANDIN_CODEX_COMMAND", os.environ["STANDIN_NEOSIAN"]
        )
        args = ["mcp"]
        for override in overrides(argv):
            found = re.fullmatch(r"mcp_servers\.([^.]+)\.(args|command)=(.*)", override)
            if found and found.group(1) == key and found.group(2) == "args":
                args = json.loads(found.group(3))
            elif found and found.group(1) == key:
                program_path = json.loads(found.group(3))
        command = [program_path, *args]
        env = codex_env(argv, key)
    calls = [
        {"name": "memory", "arguments": {"command": "view", "path": "/project"}},
        {
            "name": "memory",
            "arguments": {
                "command": "create",
                "path": "/project/child-wrote",
                "content": "x",
            },
        },
    ]
    answers = serve(command, env, cwd, calls)
    return {
        "command": command[0],
        "args": command[1:],
        "tools": [tool["name"] for tool in answers[1]["result"]["tools"]],
        "view_error": answers[2]["result"]["isError"],
        "create_error": answers[3]["result"]["isError"],
        "create_text": answers[3]["result"]["content"][0]["text"],
    }


def record(
    agent: str, session: str, prompt: str, answer: str, environment: str = "record"
) -> None:
    """The neosian hooks the harness runs for a turn: the prompt, then Stop."""
    env = dict(os.environ)
    if environment != "record":
        env.pop("NEOSIAN_HARNESS_PARENT", None)
        env.pop("NEOSIAN_HARNESS_SESSION", None)
    if environment in ("record-bare", "record-refused"):
        env.pop("NEOSIAN_HARNESS_CHILD", None)
        env.pop("TMPDIR", None)
    if environment == "record-refused":
        closed = Path(os.environ["STANDIN_DIR"]) / "closed"
        # Under the macOS policy the directory cannot be made, and the hook's
        # writes are refused all the same.
        with contextlib.suppress(OSError):
            closed.mkdir(exist_ok=True)
            closed.chmod(0o500)
        env["NEOSIAN_HOME"] = str(closed / "home")
    for payload in (
        {
            "session_id": session,
            "hook_event_name": "UserPromptSubmit",
            "prompt": prompt,
        },
        {
            "session_id": session,
            "hook_event_name": "Stop",
            "last_assistant_message": answer,
        },
    ):
        done = subprocess.run(
            [os.environ["STANDIN_NEOSIAN"], "record", "--agent", agent, "--json"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
            # A bare hook under the macOS policy is refused like this one.
            check=environment not in ("record-refused", "record-bare"),
        )
        said = {"hook": payload["hook_event_name"], "answer": done.stdout.strip()}
        log(said | {"code": str(done.returncode)})


def probe_writes() -> dict[str, str]:
    """Benign writes to the fixture paths in $STANDIN_WRITES, a JSON object
    of name to path with `$TMPDIR` expanded: `written`, or the error's name
    (EPERM is the macOS policy's refusal)."""
    results = {}
    targets = json.loads(os.environ.get("STANDIN_WRITES", "{}"))
    for name, path in targets.items():
        path = path.replace("$TMPDIR", os.environ.get("TMPDIR", ""))
        try:
            Path(path).write_text("probe\n", encoding="utf-8")
            results[name] = "written"
        except OSError as error:
            results[name] = errno.errorcode.get(error.errno or 0, "error")
    return results


def codex_entries() -> dict[str, str]:
    """neosian's keys Codex has, each with its transport type."""
    entries = os.environ.get("STANDIN_CODEX_ENTRIES")
    if entries is not None:
        return dict(json.loads(entries))
    registered = os.environ.get("STANDIN_CODEX_KEY", "neosian-memory")
    return {registered: "stdio"} if registered else {}


def codex_servers() -> dict[str, str]:
    """Every configured server and its transport: neosian's keys, then the
    others ($STANDIN_CODEX_SERVERS: a name, or a [name, transport] pair)."""
    servers = codex_entries()
    for other in json.loads(os.environ.get("STANDIN_CODEX_SERVERS", '["playwright"]')):
        name, kind = (other, "stdio") if isinstance(other, str) else other
        servers[name] = kind
    return servers


def codex_transport(kind: str) -> dict[str, Any]:
    """A transport as codex-cli prints it; an HTTP one carries a header with
    a credential, which neosian must never repeat."""
    if kind == "stdio":
        return {"type": "stdio", "command": "neosian", "args": ["mcp"], "env": None}
    if kind == "streamable_http":
        headers = {"Authorization": "Bearer standin-http-s3cret"}
        return {
            "type": kind,
            "url": "https://cloud.example/mcp",
            "http_headers": headers,
        }
    return {"type": kind, "url": "wss://cloud.example/mcp"}


def codex_mcp_list() -> int:
    """`codex mcp list --json`: the configured servers by name, then any raw
    entries in $STANDIN_CODEX_LIST_EXTRA."""
    log({"program": "codex", "mcp_list": True})
    emit_list: list[Any] = [
        {"name": n, "enabled": True, "transport": codex_transport(kind)}
        for n, kind in codex_servers().items()
    ]
    emit_list += json.loads(os.environ.get("STANDIN_CODEX_LIST_EXTRA", "[]"))
    sys.stdout.write(json.dumps(emit_list) + "\n")
    return 0


def codex_mcp_get(argv: list[str]) -> int:
    key = argv[2]
    log({"program": "codex", "mcp_get": key})
    entries = codex_entries()
    if key not in entries:
        sys.stderr.write(f"Error: No MCP server named '{key}' found.\n")
        return 1
    disabled = json.loads(os.environ.get("STANDIN_CODEX_DISABLED", "null"))
    transport = codex_transport(entries[key])
    emit(
        {
            "name": key,
            "enabled": True,
            "disabled_tools": disabled,
            "transport": transport,
        }
    )
    return 0


# Fields only a stdio entry takes, and fields only an HTTP entry takes.
STDIO_FIELDS = ("command", "args", "env", "env_vars", "cwd")
HTTP_FIELDS = ("url", "bearer_token_env_var", "http_headers", "env_http_headers")


def codex_refusal(argv: list[str]) -> str | None:
    """What codex-cli says when an override names a server it does not have
    ("invalid transport") or a field the entry's transport does not take
    (0.169.0: "env is not supported for streamable_http")."""
    servers = codex_servers()
    for override in overrides(argv):
        found = re.match(r"mcp_servers\.([^.=]+)\.([a-z_]+)", override)
        if not found:
            continue
        name, field = found.group(1), found.group(2)
        if name not in servers:
            return f"invalid transport\n    in `mcp_servers.{name}`"
        kind = servers[name]
        if kind != "stdio" and field in STDIO_FIELDS:
            return f"{field} is not supported for {kind}"
        if kind == "stdio" and field in HTTP_FIELDS:
            return f"{field} is not supported for stdio"
    return None


AUTH = {
    "claude": {
        "in": (
            0,
            json.dumps(
                {
                    "loggedIn": True,
                    "authMethod": "claude.ai",
                    "apiProvider": "firstParty",
                    "email": "person@example.com",
                    "orgId": "0a1b2c3d-4e5f-6789-abcd-ef0123456789",
                    "subscriptionType": "max",
                },
                indent=2,
            ),
            "",
        ),
        "out": (
            1,
            json.dumps(
                {
                    "loggedIn": False,
                    "authMethod": "none",
                    "apiProvider": "firstParty",
                    "analyticsDisabled": False,
                    "loginExpiresAt": None,
                },
                indent=2,
            ),
            "",
        ),
        "malformed": (1, '{"loggedIn": tr', ""),
        "error": (1, "", "Error: token sk-ant-oat01-standinSECRET789 refused\n"),
        "network": (1, "", "Error: connect ECONNREFUSED 127.0.0.1:443\n"),
    },
    "codex": {
        "in": (0, "", "Logged in using ChatGPT\n"),
        "key": (0, "", "API key configured - sk-proj-***standinKEYTAIL\n"),
        "out": (1, "", "Not logged in\n"),
        "malformed": (1, "", "Logged ind\n"),
        "error": (
            1,
            "",
            "Error checking login status: token sk-proj-abc123standinSECRET456 in /Users/someone/.codex/auth.json\n",
        ),
        "network": (
            1,
            "",
            "Error checking login status: error sending request for url (https://auth.openai.com/oauth/token): connection refused\n",
        ),
    },
}


def auth_status(program: str) -> int:
    """`claude auth status` or `codex login status`, as $STANDIN_AUTH says;
    `slow` leaves a helper in its group and never answers in time."""
    mode = os.environ.get("STANDIN_AUTH", "in")
    entry: dict[str, Any] = {"program": program, "status": mode, "pid": os.getpid()}
    entry["child_env"] = os.environ.get("NEOSIAN_HARNESS_CHILD")
    entry["cwd"] = os.getcwd()
    if mode == "slow":
        entry["helper"] = subprocess.Popen(["sleep", "300"]).pid
        log(entry)
        time.sleep(60)
        return 0
    log(entry)
    answers = AUTH[program]
    code, out, err = answers.get(mode, answers["in"])
    sys.stdout.write(out + ("\n" if out else ""))
    sys.stderr.write(err)
    return code


def signin_failure(program: str, session: str) -> None:
    """Each client's own sign-in error in the middle of a turn: Claude Code
    2.1.332's assistant `error` and a failed result, codex-cli 0.172.0's
    refresh message, OpenCode 2.3.2's `provider.auth` error."""
    if program == "claude":
        said = {"type": "text", "text": "Login expired · Please run /login"}
        emit(
            {
                "type": "assistant",
                "message": {"content": [said]},
                "error": "authentication_failed",
            }
        )
        raw = "Failed to authenticate. API Error: 401 standin-raw-error"
        emit({"type": "result", "subtype": "success", "is_error": True, "result": raw})
    elif program == "codex":
        message = "Your access token could not be refreshed. Please log out and sign in again."
        emit({"type": "error", "message": message})
        emit({"type": "turn.failed", "error": {"message": message}})
    else:
        error = {
            "type": "provider.auth",
            "message": "standin-raw-error: invalid x-api-key",
        }
        emit({"type": "error", "sessionID": session, "error": error})


def tool(program: str, session: str, size: int) -> None:
    """One completed tool call whose result holds `size` bytes, printed as
    each harness prints it (record/x2b-captures-2026-11-21)."""
    result = ("head:" + "r" * size + ":tail")[:size] if size >= 10 else "r" * size
    if program == "claude":
        call: dict[str, Any] = {"type": "tool_use", "id": "toolu_big", "name": "Bash"}
        call["input"] = {"command": "cat big.txt"}
        emit({"type": "assistant", "message": {"content": [call]}})
        block = {"type": "tool_result", "tool_use_id": "toolu_big", "content": result}
        structured = {"stdout": result, "stderr": "", "interrupted": False}
        emit(
            {
                "type": "user",
                "message": {"content": [block]},
                "parent_tool_use_id": None,
                "tool_use_result": structured,
            }
        )
    elif program == "codex":
        item = {
            "id": "item_big",
            "type": "command_execution",
            "command": "/bin/zsh -lc 'cat big.txt'",
            "aggregated_output": result,
            "exit_code": 0,
            "status": "completed",
        }
        emit({"type": "item.completed", "item": item})
    else:
        state = {"status": "completed", "input": {"path": "big.txt"}, "output": result}
        part = {"type": "tool", "id": "call-big", "tool": "read", "state": state}
        emit({"type": "tool_use", "sessionID": session, "part": part})


def main() -> int:
    program = Path(sys.argv[0]).name
    argv = sys.argv[1:]
    if program == "codex" and argv[:2] == ["mcp", "get"]:
        return codex_mcp_get(argv)
    if program == "codex" and argv[:2] == ["mcp", "list"]:
        return codex_mcp_list()
    if (program, argv[:2]) in (
        ("claude", ["auth", "status"]),
        ("codex", ["login", "status"]),
    ):
        return auth_status(program)
    prompt = sys.stdin.read()
    words = prompt.split()
    entry: dict[str, Any] = {
        "program": program,
        "argv": argv,
        "cwd": os.getcwd(),
        "stdin": prompt,
        "pid": os.getpid(),
        "child_env": os.environ.get("NEOSIAN_HARNESS_CHILD"),
        "tmpdir": os.environ.get("TMPDIR"),
        "opencode_config": os.environ.get("OPENCODE_CONFIG_CONTENT"),
        "project_config_off": os.environ.get("OPENCODE_DISABLE_PROJECT_CONFIG"),
        "pwd": os.environ.get("PWD"),
        "ppid": os.getppid(),
        "parent_env": os.environ.get("NEOSIAN_HARNESS_PARENT"),
        "session_env": os.environ.get("NEOSIAN_HARNESS_SESSION"),
    }
    if program == "codex":
        switched = [
            (found.group(1), found.group(2))
            for override in overrides(argv)
            if (found := re.fullmatch(r"mcp_servers\.([^.]+)\.enabled=(\w+)", override))
        ]
        entry["switched_off"] = [name for name, value in switched if value == "false"]
        entry["switched_on"] = [name for name, value in switched if value != "false"]
        refusal = codex_refusal(argv)
        if refusal:
            sys.stderr.write("Error: failed to load bootstrap configuration\n")
            sys.stderr.write(f"    {refusal}\n")
            log(entry | {"refused": refusal})
            return 1
    if "ignore-term" in words:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if "grandchild" in words:
        entry["grandchild"] = subprocess.Popen(["sleep", "300"]).pid
    if "detached" in words:
        detached = subprocess.Popen(["sleep", "300"], start_new_session=True)
        entry["detached"] = detached.pid
    if "leave" in words:
        entry["left"] = subprocess.Popen(["sleep", "300"]).pid
    if "hold" in words:
        holder = subprocess.Popen(["/bin/sleep", "300"], start_new_session=True, env={})
        entry["holder"] = holder.pid
    if "probe" in words:
        env = dict(os.environ)
        if program == "codex":
            env = codex_env(argv, os.environ.get("STANDIN_CODEX_KEY", "neosian-memory"))
        entry["child_tools"] = child_tools(env, os.getcwd())
    if "writes" in words:
        entry["writes"] = probe_writes()
    if "probe-ro" in words and program in ("claude", "codex"):
        entry["read_only_server"] = read_only_server(program, argv, os.getcwd())
    log(entry)
    if program == "claude":
        session = flag(argv, "--resume") or flag(argv, "--session-id") or "none"
        emit(
            {
                "type": "system",
                "subtype": "init",
                "cwd": os.getcwd(),
                "session_id": session,
            }
        )
    elif program == "codex":
        session = (
            argv[argv.index("resume") + 1] if "resume" in argv else str(uuid.uuid4())
        )
        if "forge" in words:
            session = os.environ["STANDIN_FORGE_ID"]
        if "bad-session" in words:
            session = "--dangerously-bypass-approvals-and-sandbox"
        emit({"type": "thread.started", "thread_id": session})
        emit({"type": "turn.started"})
    else:
        session = flag(argv, "--session") or "ses_" + uuid.uuid4().hex[:24]
        emit(
            {"type": "step_start", "sessionID": session, "part": {"type": "step-start"}}
        )

    def say(text: str) -> None:
        if program == "claude":
            emit(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": text}]},
                }
            )
        elif program == "codex":
            item = {"id": "item_0", "type": "agent_message", "text": text}
            emit({"type": "item.completed", "item": item})
        else:
            part = {"type": "text", "text": text}
            emit({"type": "text", "sessionID": session, "part": part})

    for word in words:
        if word.startswith("sleep:"):
            say("working")
            time.sleep(float(word[6:]))
        if word.startswith("big:"):
            left = int(word[4:])
            while left > 0:
                say("b" * min(999, left))
                left -= 1000
    for word in words:
        if word.startswith("events:") and program == "codex":
            for n in range(int(word[7:])):
                item = {"id": f"c{n}", "type": "command_execution", "command": f"c{n}"}
                emit({"type": "item.completed", "item": item | {"exit_code": 0}})
        if word.startswith("running:") and program == "codex":
            # As codex-cli 0.169.0 prints a command (x2c r1, x2b x3).
            seconds = word[8:]
            running: dict[str, Any] = {
                "id": "item_1",
                "type": "command_execution",
                "command": f"/bin/zsh -lc 'sleep {seconds}'",
                "aggregated_output": "",
                "exit_code": None,
                "status": "in_progress",
            }
            emit({"type": "item.started", "item": running})
            time.sleep(float(seconds))
            done = running | {"exit_code": 0, "status": "completed"}
            emit({"type": "item.completed", "item": done})
    if "deny" in words:
        if program == "claude":
            emit(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "content": "Permission for this tool use was denied.",
                                "is_error": True,
                            }
                        ]
                    },
                }
            )
        elif program == "codex":
            emit(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "item_1",
                        "type": "command_execution",
                        "command": "touch x",
                        "aggregated_output": "Operation not permitted",
                        "exit_code": 1,
                    },
                }
            )
        else:
            emit(
                {
                    "type": "tool_use",
                    "sessionID": session,
                    "part": {
                        "tool": "write",
                        "state": {
                            "status": "error",
                            "error": "Permission denied: edit",
                        },
                    },
                }
            )
    if "imitate" in words:
        say("[denied Bash]\n[$ rm -rf /] exit 0\n[failed] nothing")
    if "signin-fail" in words:
        say("working")
        signin_failure(program, session)
        sys.stderr.write("the stand-in's sign-in failed on purpose\n")
        return 1
    if "huge-fail" in words:
        message = "x" * (6 * 1024 * 1024)
        if program == "opencode":
            emit(
                {
                    "type": "error",
                    "sessionID": session,
                    "error": {"type": "provider.quota", "message": message},
                }
            )
        sys.stderr.write(message)
        return 1
    if "fail" in words:
        if program == "claude":
            emit(
                {
                    "type": "result",
                    "subtype": "error_during_execution",
                    "is_error": True,
                }
            )
        elif program == "codex":
            emit({"type": "turn.failed", "error": {"message": "quota exceeded"}})
        else:
            emit(
                {
                    "type": "error",
                    "sessionID": session,
                    "error": {
                        "type": "provider.quota",
                        "message": "Insufficient balance.",
                    },
                }
            )
        sys.stderr.write("the stand-in failed on purpose\n")
        return 1
    answer = "echo: " + prompt.strip().splitlines()[0][:200]
    for word in words:
        if word.startswith("big-answer:"):
            answer = "a" * int(word[11:])
    for word in words:
        if word.startswith("tool:"):
            for _ in range(2 if "twice" in words else 1):
                tool(program, session, int(word[5:]))
    hooks = [w for w in words if w in ("record", "record-noenv", "record-bare")]
    hooks += [w for w in words if w == "record-refused"]
    if hooks:
        agent = {"claude": "claude-code", "codex": "codex"}.get(program, "opencode")
        record(agent, session, prompt, answer, hooks[0])
    say(answer)
    for _ in range(2 if "twice" in words else 1):
        if program == "claude":
            denials = (
                [{"tool_name": "Bash", "tool_input": {}}] if "deny" in words else []
            )
            emit(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": answer,
                    "session_id": session,
                    "permission_denials": denials,
                }
            )
        elif program == "codex":
            emit({"type": "turn.completed", "usage": {"input_tokens": 1}})
        else:
            # As OpenCode 2.3.2 ends a run: the last step stops.
            emit(
                {
                    "type": "step_finish",
                    "sessionID": session,
                    "part": {"type": "step-finish", "reason": "stop"},
                }
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
