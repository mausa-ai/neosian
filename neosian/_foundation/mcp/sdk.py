"""The one place the MCP SDK is imported (function-local, on first use).

Every other module in this package is importable without the SDK
installed — `import neosian` and `import neosian.mcp` stay SDK-free,
pinned by subprocess tests. Absolute imports mean `from mcp.server
import …` always resolves to the third-party `mcp` distribution, never
to `neosian.mcp` or this package. Two loaders: the server side
(`load_sdk`, N4) and the client side (`load_client_sdk`, NC1 — §25).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.client import Client
    from mcp.client.stdio import StdioServerParameters
    from mcp.server import Server
    from mcp.shared.exceptions import MCPError
    from mcp.types import (
        CallToolResult,
        GetPromptResult,
        ListPromptsResult,
        ListToolsResult,
        Prompt,
        PromptMessage,
        TextContent,
        Tool,
        ToolAnnotations,
    )

_INSTALL_HINT = (
    "neosian's MCP support needs the mcp SDK, which the neosian install "
    "carries — reinstall: uv add neosian (or pip install neosian)"
)


@dataclass(frozen=True, slots=True)
class Sdk:
    """The loaded SDK surface: the low-level server class and the wire
    types the handlers construct — tools, and prompts for skills (§24)."""

    server_class: type[Server[Any]]
    tool: type[Tool]
    tool_annotations: type[ToolAnnotations]
    text: type[TextContent]
    list_tools_result: type[ListToolsResult]
    call_tool_result: type[CallToolResult]
    prompt: type[Prompt]
    prompt_message: type[PromptMessage]
    list_prompts_result: type[ListPromptsResult]
    get_prompt_result: type[GetPromptResult]
    # A raised `error(code=invalid_params, …)` is the JSON-RPC error reply
    # a prompt request for an unknown skill gets — never an internal error.
    error: type[MCPError]
    invalid_params: int


def load_sdk() -> Sdk:
    """Import the MCP SDK, raising a helpful ImportError when it is missing."""
    try:
        from mcp.server import Server
        from mcp.shared.exceptions import MCPError
        from mcp.types import (
            INVALID_PARAMS,
            CallToolResult,
            GetPromptResult,
            ListPromptsResult,
            ListToolsResult,
            Prompt,
            PromptMessage,
            TextContent,
            Tool,
            ToolAnnotations,
        )
    except ImportError as exc:
        raise ImportError(_INSTALL_HINT) from exc
    return Sdk(
        server_class=Server,
        tool=Tool,
        tool_annotations=ToolAnnotations,
        text=TextContent,
        list_tools_result=ListToolsResult,
        call_tool_result=CallToolResult,
        prompt=Prompt,
        prompt_message=PromptMessage,
        list_prompts_result=ListPromptsResult,
        get_prompt_result=GetPromptResult,
        error=MCPError,
        invalid_params=INVALID_PARAMS,
    )


@dataclass(frozen=True, slots=True)
class ClientSdk:
    """The loaded client surface: the high-level client and the transport
    factories `McpServer` hands it (§25). The SDK speaks its own httpx
    fork, so its HTTP client comes from its helper — neosian never
    imports that package."""

    client: type[Client]
    stdio_client: Callable[..., Any]
    stdio_parameters: type[StdioServerParameters]
    streamable_http_client: Callable[..., Any]
    http_client: Callable[..., Any]


def load_client_sdk() -> ClientSdk:
    """Import the SDK's client side, the same ImportError when it is missing."""
    try:
        from mcp.client import Client
        from mcp.client.stdio import StdioServerParameters, stdio_client
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client
    except ImportError as exc:
        raise ImportError(_INSTALL_HINT) from exc
    return ClientSdk(
        client=Client,
        stdio_client=stdio_client,
        stdio_parameters=StdioServerParameters,
        streamable_http_client=streamable_http_client,
        http_client=create_mcp_http_client,
    )
