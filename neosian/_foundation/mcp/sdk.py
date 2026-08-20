"""The one place the MCP SDK is imported (function-local, on first use).

Every other module in this package is importable without the SDK
installed — `import neosian` and `import neosian.mcp` stay SDK-free,
pinned by subprocess tests. Absolute imports mean `from mcp.server
import …` always resolves to the third-party `mcp` distribution, never
to `neosian.mcp` or this package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.server import Server
    from mcp.types import (
        CallToolResult,
        ListToolsResult,
        TextContent,
        Tool,
        ToolAnnotations,
    )

_INSTALL_HINT = (
    "The neosian MCP memory server requires the 'mcp' extra — "
    "uv add 'neosian[mcp]' (or pip install 'neosian[mcp]')"
)


@dataclass(frozen=True, slots=True)
class Sdk:
    """The loaded SDK surface: the low-level server class and the wire
    types the handlers construct."""

    server_class: type[Server[Any]]
    tool: type[Tool]
    tool_annotations: type[ToolAnnotations]
    text: type[TextContent]
    list_tools_result: type[ListToolsResult]
    call_tool_result: type[CallToolResult]


def load_sdk() -> Sdk:
    """Import the MCP SDK, raising a helpful ImportError without the extra."""
    try:
        from mcp.server import Server
        from mcp.types import (
            CallToolResult,
            ListToolsResult,
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
    )
