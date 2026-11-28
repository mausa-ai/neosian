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
For the watcher's record (X4a): `tool:N` (one completed tool call whose
result holds N bytes, in each harness's own shape), `twice` (the tool call
and the completion printed twice), `record-bare` (the hooks run without any
of the harness variables, as an older hook binary or a harness that clears
its hooks' environment would) and `record-refused` (the hooks run where they
cannot write the spool or the store, as under a sandbox).
"""

from __future__ import annotations

import json
import os
import re
import signal
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
            check=environment != "record-refused",
        )
        log({"hook": payload["hook_event_name"], "answer": done.stdout.strip()})


def codex_mcp_list() -> int:
    """`codex mcp list --json`: the configured servers by name, then any raw
    entries in $STANDIN_CODEX_LIST_EXTRA."""
    log({"program": "codex", "mcp_list": True})
    registered = os.environ.get("STANDIN_CODEX_KEY", "neosian-memory")
    others = json.loads(os.environ.get("STANDIN_CODEX_SERVERS", '["playwright"]'))
    names = ([registered] if registered else []) + others
    emit_list: list[Any] = [
        {"name": n, "enabled": True, "transport": {"type": "stdio"}} for n in names
    ]
    emit_list += json.loads(os.environ.get("STANDIN_CODEX_LIST_EXTRA", "[]"))
    sys.stdout.write(json.dumps(emit_list) + "\n")
    return 0


def codex_mcp_get(argv: list[str]) -> int:
    key = argv[2]
    log({"program": "codex", "mcp_get": key})
    if key != os.environ.get("STANDIN_CODEX_KEY", "neosian-memory"):
        sys.stderr.write(f"Error: No MCP server named '{key}' found.\n")
        return 1
    disabled = json.loads(os.environ.get("STANDIN_CODEX_DISABLED", "null"))
    emit({"name": key, "enabled": True, "disabled_tools": disabled, "transport": {}})
    return 0


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
        registered = os.environ.get("STANDIN_CODEX_KEY", "neosian-memory")
        known = [registered] + json.loads(
            os.environ.get("STANDIN_CODEX_SERVERS", '["playwright"]')
        )
        entry["switched_off"] = [
            found.group(1)
            for override in overrides(argv)
            if (found := re.fullmatch(r"mcp_servers\.([^.]+)\.enabled=false", override))
        ]
        for override in overrides(argv):
            found = re.match(r"mcp_servers\.([^.]+)\.", override)
            if found and found.group(1) not in known:
                sys.stderr.write("Error: failed to load bootstrap configuration\n")
                sys.stderr.write(
                    f"    invalid transport\n    in `mcp_servers.{found.group(1)}`\n"
                )
                log(entry | {"refused": True})
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
