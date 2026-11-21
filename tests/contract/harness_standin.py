"""A stand-in for the claude, codex and opencode headless commands.

Built from the real runs in record/x2b-captures-2026-11-21 (Claude Code
2.1.330, codex-cli 0.169.0, OpenCode 2.3.2): the same argv forms, the same
JSON events on stdout, the prompt read from stdin. It logs what it was
given to $STANDIN_DIR/log.jsonl. Words in the prompt choose a behavior:
`sleep:N`, `big:N`, `fail`, `deny`, `ignore-term`, `grandchild`, `bad-session`,
`record`
(run the neosian record hooks for the turn) and `probe` (start the child's
own neosian MCP server as the harness would and list its tools; Codex's
stand-in gives that server only the variables real Codex passes).
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
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    done = subprocess.run(
        [os.environ["STANDIN_NEOSIAN"], "mcp"],
        input="".join(json.dumps(line) + "\n" for line in lines),
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        timeout=30,
        check=False,
    )
    answers = [json.loads(line) for line in done.stdout.splitlines()]
    return [tool["name"] for tool in answers[-1]["result"]["tools"]]


def record(agent: str, session: str, prompt: str, answer: str) -> None:
    """The neosian hooks the harness runs for a turn: the prompt, then Stop."""
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
        subprocess.run(
            [os.environ["STANDIN_NEOSIAN"], "record", "--agent", agent, "--json"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )


def codex_mcp_get(argv: list[str]) -> int:
    key = argv[2]
    log({"program": "codex", "mcp_get": key})
    if key != os.environ.get("STANDIN_CODEX_KEY", "neosian-memory"):
        sys.stderr.write(f"Error: No MCP server named '{key}' found.\n")
        return 1
    disabled = json.loads(os.environ.get("STANDIN_CODEX_DISABLED", "null"))
    emit({"name": key, "enabled": True, "disabled_tools": disabled, "transport": {}})
    return 0


def main() -> int:
    program = Path(sys.argv[0]).name
    argv = sys.argv[1:]
    if program == "codex" and argv[:2] == ["mcp", "get"]:
        return codex_mcp_get(argv)
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
    }
    if program == "codex":
        registered = os.environ.get("STANDIN_CODEX_KEY", "neosian-memory")
        for override in overrides(argv):
            found = re.match(r"mcp_servers\.([^.]+)\.", override)
            if found and found.group(1) != registered:
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
    if "probe" in words:
        env = dict(os.environ)
        if program == "codex":
            env = codex_env(argv, os.environ.get("STANDIN_CODEX_KEY", "neosian-memory"))
        entry["child_tools"] = child_tools(env, os.getcwd())
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
    if "record" in words:
        agent = {"claude": "claude-code", "codex": "codex"}.get(program, "opencode")
        record(agent, session, prompt, answer)
    say(answer)
    if program == "claude":
        denials = [{"tool_name": "Bash", "tool_input": {}}] if "deny" in words else []
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
        emit(
            {
                "type": "step_finish",
                "sessionID": session,
                "part": {"type": "step-finish"},
            }
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
