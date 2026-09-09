"""`McpServer` — an MCP server consumed as tools (NC1, DESIGN §25).

The caller owns the lifetime: `async with McpServer.stdio(...) as server`
connects through the official client, lists the tools once, and exposes
them as plain tool functions for `AgentConfig(tools=[*server.tools])`.
The core stays stateless, and every seam — the approval gate, hooks,
Conversation's per-boundary rebuild, link expansion — applies by
construction, because a bridged tool is an ordinary tool. The SDK loads
at connect time; importing this module never loads it.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from neosian._foundation.mcp.bridge import bridge_definition, bridge_tool
from neosian._foundation.mcp.sdk import ClientSdk, load_client_sdk
from neosian._foundation.shared.exceptions import McpConnectionError
from neosian._foundation.shared.types import ToolFunction

_NOT_CONNECTED = "McpServer '{name}' is not connected — use `async with`"
_DISCONNECTED = "MCP server '{name}' is disconnected"
_DUPLICATE_WIRE_NAME = "the server lists the tool '{name}' twice"


@dataclass(frozen=True, slots=True)
class _Stdio:
    command: str
    args: tuple[str, ...]
    env: Mapping[str, str] | None
    cwd: str | None

    @property
    def label(self) -> str:
        return PurePath(self.command).name

    def open(self, sdk: ClientSdk) -> Any:
        parameters = sdk.stdio_parameters(
            command=self.command,
            args=list(self.args),
            env=None if self.env is None else dict(self.env),
            cwd=self.cwd,
        )
        return sdk.stdio_client(parameters)


@dataclass(frozen=True, slots=True)
class _Http:
    url: str
    headers: Mapping[str, str] | None

    @property
    def label(self) -> str:
        return self.url

    def open(self, sdk: ClientSdk) -> Any:
        if self.headers is None:
            return self.url
        http_client = sdk.http_client(headers=dict(self.headers))
        return sdk.streamable_http_client(self.url, http_client=http_client)


@dataclass(frozen=True, slots=True)
class _InProcess:
    server: Any

    @property
    def label(self) -> str:
        return str(getattr(self.server, "name", "in-process"))

    def open(self, _sdk: ClientSdk) -> Any:
        return self.server


_Endpoint = _Stdio | _Http | _InProcess


class McpServer:
    """One MCP server, consumed as tools for the lifetime of an `async with`.

    Entering connects (the SDK's client over the endpoint), lists the
    tools once — following pagination — and bridges each into a tool
    function carrying the server's schema verbatim; `tools` is that
    tuple. Exiting closes the client; a tool captured earlier then fails
    in-band, naming the server. Entering again opens a fresh client.

    Names are the server's own unless `prefix=` is given
    (`prefix="gh"` → `gh__search`); a collision with another tool is the
    core's `ConfigurationError` at Agent construction, which names this
    server. A per-call failure — a server error, a lost transport, a
    bad argument — is an in-band `ToolResult.fail` the model sees; only
    connecting raises (`McpConnectionError`).
    """

    def __init__(
        self, endpoint: _Endpoint, *, name: str | None, prefix: str | None
    ) -> None:
        self._endpoint = endpoint
        self._name = endpoint.label if name is None else name
        self._prefix = prefix
        self._stack: AsyncExitStack | None = None
        self._client: Any = None
        self._bridged: tuple[ToolFunction, ...] = ()

    @classmethod
    def stdio(
        cls,
        command: str,
        args: tuple[str, ...] | list[str] = (),
        *,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        name: str | None = None,
        prefix: str | None = None,
    ) -> McpServer:
        """A server spawned as a subprocess, spoken to over stdin/stdout.

        `env` is merged over the SDK's filtered inheritance (PATH, HOME
        and the like — never the whole environment), so a secret the
        server needs is passed here explicitly. The default `name` is the
        command's basename.
        """
        endpoint = _Stdio(command, tuple(args), env, cwd)
        return cls(endpoint, name=name, prefix=prefix)

    @classmethod
    def http(
        cls,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        name: str | None = None,
        prefix: str | None = None,
    ) -> McpServer:
        """A server at a streamable-HTTP endpoint (the daemon's `/mcp`,
        or any remote); `headers` carries a bearer token. The default
        `name` is the URL."""
        return cls(_Http(url, headers), name=name, prefix=prefix)

    @classmethod
    def in_process(
        cls, server: Any, *, name: str | None = None, prefix: str | None = None
    ) -> McpServer:
        """An SDK server object connected in-process — no socket, no
        subprocess: the keyless door (`create_memory_server`, a test
        server, the harness's `mcp` column). The default `name` is the
        server's."""
        return cls(_InProcess(server), name=name, prefix=prefix)

    @property
    def name(self) -> str:
        return self._name

    @property
    def prefix(self) -> str | None:
        return self._prefix

    @property
    def tools(self) -> tuple[ToolFunction, ...]:
        """The bridged tools, in the server's listing order."""
        if self._stack is None:
            raise RuntimeError(_NOT_CONNECTED.format(name=self._name))
        return self._bridged

    async def __aenter__(self) -> McpServer:
        sdk = load_client_sdk()
        stack = AsyncExitStack()
        try:
            client = await stack.enter_async_context(
                sdk.client(self._endpoint.open(sdk))
            )
            tools = self._bridge(await _list_all(client))
        except Exception as exc:
            await stack.aclose()
            raise McpConnectionError(self._name, _leaf(exc)) from exc
        self._stack, self._client, self._bridged = stack.pop_all(), client, tools
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        # Torn down as on a clean exit, never handed the body's exception:
        # the SDK's task group would otherwise re-raise it wrapped in an
        # ExceptionGroup, hiding it from the caller's `except`.
        stack, self._stack, self._client, self._bridged = self._stack, None, None, ()
        if stack is not None:
            await stack.aclose()

    def _bridge(self, listing: list[Any]) -> tuple[ToolFunction, ...]:
        origin = f"MCP server '{self._name}'"
        seen: set[str] = set()
        tools: list[ToolFunction] = []
        for tool in listing:
            if tool.name in seen:
                raise ValueError(_DUPLICATE_WIRE_NAME.format(name=tool.name))
            seen.add(tool.name)
            definition = bridge_definition(tool, prefix=self._prefix)
            tools.append(bridge_tool(self._call, tool.name, definition, origin=origin))
        return tuple(tools)

    async def _call(self, wire_name: str, arguments: dict[str, Any]) -> Any:
        if self._client is None:
            raise RuntimeError(_DISCONNECTED.format(name=self._name))
        return await self._client.call_tool(wire_name, arguments)


async def _list_all(client: Any) -> list[Any]:
    """Every listed tool, across pages."""
    tools: list[Any] = []
    cursor: str | None = None
    while True:
        page = await client.list_tools(cursor=cursor)
        tools.extend(page.tools)
        cursor = page.next_cursor
        if cursor is None:
            return tools


def _leaf(exc: BaseException) -> BaseException:
    """A single-leaf task group (the SDK's handshake shape) unwrapped, so
    the connection error names the cause, not the group."""
    while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
        exc = exc.exceptions[0]
    return exc
