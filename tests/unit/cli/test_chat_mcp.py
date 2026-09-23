"""`[[chat.mcp]]` (NC8 slice B; DESIGN §30.2): the tables' grammar, the
servers open for a turn or a session with their tools added, a name chat
already has refused with the prefix hint, a failed connection at tier 1
naming the server, keyless, the real stdio subprocess of `python -m
neosian.mcp` included."""

import io
import json
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer
from rich.console import Console

from neosian import (
    AgentConfig,
    ConfigurationError,
    McpConnectionError,
    Model,
    Tool,
    ToolResult,
)
from neosian._cli.chat import run_chat
from neosian._cli.chat_agent import resident_config
from neosian._cli.chat_cmd import one_shot, run_chat_command
from neosian._cli.chat_mcp import RESIDENT_TOOLS, chat_servers, serving
from neosian._cli.config import write_config
from neosian._cli.playground import run_playground
from neosian._foundation.llm.base import ToolCall
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.file import FileStore
from neosian._foundation.shared.types import ToolCallId, ToolFunction, ToolName
from neosian._foundation.tools.base import get_tool_metadata
from neosian.mcp import McpServer

_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CEREBRAS_API_KEY")
_NOPE = "definitely-not-a-binary-xyz"
_AGENT_FILE = (
    "from neosian import AgentConfig, Model\n"
    "configuration = AgentConfig(system_prompt='x', model=Model.FAKE)\n"
)


@pytest.fixture(autouse=True)
def _keyless(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in _KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


def _probe(*extra: str) -> tuple[MCPServer, list[dict[str, Any]]]:
    """A small SDK server: `add` (recording its calls), `media`, and any
    `extra` name as a stub: a collision on demand."""
    server = MCPServer("probe")
    calls: list[dict[str, Any]] = []

    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two integers."""
        calls.append({"a": a, "b": b})
        return a + b

    @server.tool()
    def media() -> str:
        return "m"

    for name in extra:

        def stub() -> str:
            return "x"

        server.tool(name=name)(stub)
    return server, calls


def _names(tools: Sequence[ToolFunction]) -> list[str]:
    return [str(m.name) for t in tools if (m := get_tool_metadata(t)) is not None]


def _script(name: str, arguments: dict[str, Any]) -> FakeScript:
    call = ToolCall(id=ToolCallId("c1"), name=ToolName(name), arguments=arguments)
    return FakeScript(
        turns=(FakeTurn(tool_calls=(call,)), FakeTurn(content="finished"))
    )


def _config(script: FakeScript | None = None) -> AgentConfig:
    fake = FakeClient(script or FakeScript(turns=(FakeTurn(content="noted"),)))
    return replace(resident_config(Model.FAKE), client_factory=lambda _: fake)


def _stdio(tmp_path: Path, **extra: Any) -> dict[str, Any]:
    """The memory server's table; `--root` always, since the SDK hands a
    child only PATH, HOME and the like, never `NEOSIAN_HOME`."""
    args = [
        "-m",
        "neosian.mcp",
        "--root",
        str(tmp_path / "mem"),
        "--scope",
        "user:demo",
    ]
    return {"name": "mem", "command": sys.executable, "args": args, **extra}


def _run(
    prompt: str | None, *, agent: str | None = None, json_output: bool = False
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = run_chat_command(
        prompt,
        model="fake",
        agent=agent,
        resume=None,
        json_output=json_output,
        stdin=io.StringIO(),
        out=out,
        err=err,
    )
    return code, out.getvalue(), err.getvalue()


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.unit
class TestTheGrammar:
    def test_absent_or_empty_is_no_server(self) -> None:
        assert chat_servers({}) == ()
        assert chat_servers({"mcp": []}) == ()
        assert chat_servers({"model": "fake"}) == ()

    def test_a_stdio_and_an_http_table(self) -> None:
        gh = {"name": "gh", "command": "npx", "args": ["-y", "x"], "env": {"T": "1"}}
        daemon = {
            "name": "daemon",
            "url": "http://127.0.0.1:1/mcp",
            "headers": {"Authorization": "Bearer t"},
            "prefix": "d",
        }
        servers = chat_servers({"mcp": [gh, daemon]})
        assert [(s.name, s.prefix) for s in servers] == [("gh", None), ("daemon", "d")]

    def test_not_a_list(self) -> None:
        with pytest.raises(ValueError, match=r"\[\[chat.mcp\]\] must be a list"):
            chat_servers({"mcp": {"name": "x"}})

    @pytest.mark.parametrize(
        ("tables", "message"),
        [
            (["x"], "#1: not a table"),
            ([{"name": "a", "command": "c"}, "x"], "#2: not a table"),
            ([{"command": "c"}], "#1: name must be a non-empty string"),
            ([{"name": "", "command": "c"}], "#1: name must be a non-empty string"),
            ([{"name": 3, "command": "c"}], "#1: name must be a non-empty string"),
            (
                [{"name": "a", "command": "c"}, {"name": "a", "url": "u"}],
                "a: the name is taken by an earlier table",
            ),
            (
                [{"name": "a", "command": "c", "url": "u"}],
                "a: command and url are two servers, not one",
            ),
            ([{"name": "a"}], "a: a command to spawn or a url to reach"),
            ([{"name": "a", "command": 1}], "a: command must be a string"),
            ([{"name": "a", "url": ["u"]}], "a: url must be a string"),
            ([{"name": "a", "url": "u", "prefix": 1}], "a: prefix must be a string"),
            (
                [{"name": "a", "command": "c", "args": "x"}],
                "a: args must be a list of strings",
            ),
            (
                [{"name": "a", "command": "c", "args": [1]}],
                "a: args must be a list of strings",
            ),
            (
                [{"name": "a", "command": "c", "env": {"K": 1}}],
                "a: env must be a table of strings",
            ),
            (
                [{"name": "a", "url": "u", "headers": "x"}],
                "a: headers must be a table of strings",
            ),
            (
                [{"name": "a", "command": "c", "headers": {}}],
                "a: 'headers' is not a key of a command table",
            ),
            (
                [{"name": "a", "url": "u", "args": []}],
                "a: 'args' is not a key of a url table",
            ),
            (
                [{"name": "a", "command": "c", "cwd": "/"}],
                "a: 'cwd' is not a key of a command table",
            ),
        ],
    )
    def test_a_malformed_table_is_named(self, tables: list[Any], message: str) -> None:
        with pytest.raises(ValueError) as info:
            chat_servers({"mcp": tables})
        assert str(info.value) == f"[[chat.mcp]] {message}"


@pytest.mark.unit
class TestServing:
    async def test_no_servers_is_the_config_itself(self) -> None:
        config = _config()
        async with serving((), config) as served:
            assert served is config

    async def test_the_servers_tools_join_the_agents(self) -> None:
        server, _ = _probe()
        config = _config()
        async with serving((McpServer.in_process(server),), config) as served:
            assert _names(served.tools) == ["docs", "add", "media"]
        assert _names(config.tools) == ["docs"]  # never mutated

    async def test_a_name_chat_has_is_refused_with_the_hint(self) -> None:
        @Tool(name=ToolName("add"), description="local add")
        async def add(a: int, b: int) -> ToolResult[int]:
            return ToolResult.ok(a + b)

        server, _ = _probe()
        config = replace(_config(), tools=[add])
        hint = "MCP server 'probe' serves 'add', which chat already has: set prefix"
        with pytest.raises(ConfigurationError, match=hint):
            async with serving((McpServer.in_process(server),), config):
                pass

    @pytest.mark.parametrize("name", RESIDENT_TOOLS)
    async def test_the_conversations_own_names_are_refused(self, name: str) -> None:
        """`recall_turn` included: the Agent registers it lazily, so its
        collision would otherwise surface at the first compaction."""
        server, _ = _probe(name)
        with pytest.raises(ConfigurationError, match=f"serves '{name}'"):
            async with serving((McpServer.in_process(server),), _config()):
                pass
        prefixed = McpServer.in_process(server, prefix="p")
        async with serving((prefixed,), _config()) as served:
            assert f"p__{name}" in _names(served.tools)

    async def test_update_todo_is_refused_only_when_on(self) -> None:
        server, _ = _probe("update_todo")
        config = _config()  # the resident agent keeps todo off
        async with serving((McpServer.in_process(server),), config) as served:
            assert "update_todo" in _names(served.tools)
        with pytest.raises(ConfigurationError, match="serves 'update_todo'"):
            async with serving(
                (McpServer.in_process(server),), replace(config, enable_todo=True)
            ):
                pass

    async def test_two_servers_that_clash(self) -> None:
        first, _ = _probe()
        second, _ = _probe()
        servers = (McpServer.in_process(first), McpServer.in_process(second, name="b"))
        with pytest.raises(ConfigurationError, match="MCP server 'b' serves 'add'"):
            async with serving(servers, _config()):
                pass

    async def test_a_failed_connection_unwinds_the_earlier(self) -> None:
        server, _ = _probe()
        first = McpServer.in_process(server)
        with pytest.raises(McpConnectionError, match=_NOPE):
            async with serving((first, McpServer.stdio(_NOPE)), _config()):
                pass
        with pytest.raises(RuntimeError, match="not connected"):
            _ = first.tools

    async def test_a_body_exception_propagates_as_itself(self, tmp_path: Path) -> None:
        (server,) = chat_servers({"mcp": [_stdio(tmp_path, prefix="mem")]})
        with pytest.raises(KeyError):
            async with serving((server,), _config()):
                raise KeyError("body")


@pytest.mark.unit
class TestOneShot:
    async def test_a_turn_through_a_servers_tool(self, tmp_path: Path) -> None:
        server, calls = _probe()
        out = io.StringIO()
        await one_shot(
            _config(_script("add", {"a": 1, "b": 2})),
            "add",
            conversation_id="c1",
            json_output=True,
            out=out,
            servers=(McpServer.in_process(server),),
        )
        payload = json.loads(out.getvalue())
        assert payload["text"] == "finished"
        assert payload["tool_calls"] == [{"name": "add", "arguments": {"a": 1, "b": 2}}]
        assert calls == [{"a": 1, "b": 2}]
        assert len(await FileStore(tmp_path / "home").read_turns("c1")) == 1

    async def test_the_prefixed_memory_server_end_to_end(self, tmp_path: Path) -> None:
        """The ruled pin: the real stdio subprocess, its `memory` renamed
        `mem__memory`, writing through chat's turn."""
        (server,) = chat_servers({"mcp": [_stdio(tmp_path, prefix="mem")]})
        create = {"command": "create", "path": "/memories/a", "content": "b"}
        out = io.StringIO()
        await one_shot(
            _config(_script("mem__memory", create)),
            "go",
            conversation_id="c2",
            json_output=False,
            out=out,
            servers=(server,),
        )
        assert out.getvalue() == "finished\n"
        doc = await FileStore(tmp_path / "mem").read("user:demo", "a")
        assert doc is not None and doc.content == "b"


@pytest.mark.unit
class TestRunTier:
    """Through `run_chat_command`: the tables read from config.toml, the
    real `python -m neosian.mcp` spawned, the tiers."""

    def test_the_memory_server_unprefixed_is_refused(self, tmp_path: Path) -> None:
        write_config({"chat": {"mcp": [_stdio(tmp_path)]}})
        code, out, err = _run("hi", json_output=True)
        assert code == 1
        assert "error: MCP server 'mem' serves 'memory'" in err and "prefix" in err
        assert json.loads(out)["error"].startswith("MCP server 'mem' serves")

    def test_prefixed_it_serves_the_turn(self, tmp_path: Path) -> None:
        write_config({"chat": {"mcp": [_stdio(tmp_path, prefix="mem")]}})
        code, out, err = _run("hi")
        assert code == 0 and out == "fake response\n" and err == ""

    def test_a_failed_connection_names_the_server(self) -> None:
        write_config({"chat": {"mcp": [{"name": "nope", "command": _NOPE}]}})
        code, out, err = _run("hi", json_output=True)
        assert code == 1
        assert "error: MCP server 'nope' could not be connected" in err
        assert out.count("\n") == 1 and "'nope'" in json.loads(out)["error"]

    def test_a_malformed_table_is_grammar_and_spawns_nothing(self) -> None:
        table = {"name": "nope", "command": _NOPE, "cwd": "/"}
        write_config({"chat": {"mcp": [table]}})
        code, out, err = _run("hi")
        assert code == 2 and out == ""  # 1 would mean the spawn was tried
        assert "[[chat.mcp]] nope: 'cwd' is not a key of a command table" in err

    def test_an_agent_file_gets_the_servers_too(self, tmp_path: Path) -> None:
        agent = tmp_path / "my_agent.py"
        agent.write_text(_AGENT_FILE)
        write_config({"chat": {"mcp": [{"name": "nope", "command": _NOPE}]}})
        code, _, err = _run("hi", agent=str(agent))
        assert code == 1 and "MCP server 'nope' could not be connected" in err

    def test_playground_runs_the_file_as_written(self, tmp_path: Path) -> None:
        agent = tmp_path / "my_agent.py"
        agent.write_text(_AGENT_FILE)
        write_config({"chat": {"mcp": [{"name": "nope", "command": _NOPE}]}})
        out, err = io.StringIO(), io.StringIO()
        code = run_playground(
            str(agent),
            model="fake",
            menu=False,
            resume=None,
            json_output=False,
            stdin=io.StringIO("hi\n"),
            out=out,
            err=err,
        )
        assert code == 0 and out.getvalue() == "fake response\n"

    async def test_the_resident_names_are_the_memory_servers(
        self, tmp_path: Path
    ) -> None:
        (server,) = chat_servers({"mcp": [_stdio(tmp_path)]})
        async with server as s:
            assert tuple(_names(s.tools)) == RESIDENT_TOOLS


@pytest.mark.unit
class TestTheSessionLoop:
    async def test_the_banner_names_the_servers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server, _ = _probe()
        monkeypatch.setattr("sys.stdin", io.StringIO("hi\n/quit\n"))
        out = io.StringIO()
        await run_chat(
            Console(file=out, width=120, no_color=True),
            _config(),
            "probe",
            conversation_id="s1",
            resumed=False,
            servers=(McpServer.in_process(server),),
        )
        assert "MCP: probe (2 tools)" in out.getvalue()
        assert len(await FileStore(tmp_path / "home").read_turns("s1")) == 1

    async def test_a_refused_server_is_not_a_start_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("/quit\n"))
        out = io.StringIO()
        with pytest.raises(McpConnectionError):
            await run_chat(
                Console(file=out, width=120, no_color=True),
                _config(),
                "probe",
                conversation_id="s2",
                resumed=False,
                servers=(McpServer.stdio(_NOPE),),
            )
        assert "Error starting conversation" not in out.getvalue()

    def test_the_run_tier_answers_at_tier_1(self) -> None:
        write_config({"chat": {"mcp": [{"name": "nope", "command": _NOPE}]}})
        out, err = io.StringIO(), io.StringIO()
        code = run_chat_command(
            None,
            model="fake",
            agent=None,
            resume=None,
            json_output=False,
            stdin=_Terminal(),
            out=out,
            err=err,
        )
        assert code == 1 and out.getvalue() == ""
        assert "error: MCP server 'nope' could not be connected" in err.getvalue()
