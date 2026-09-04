"""The bridge: an MCP server's tools as neosian tools (NC1, DESIGN §25).

Pure mappings, SDK-free at runtime — they duck-type on the wire objects
the official client returns (`Tool`, `CallToolResult` and its content
blocks), so this module imports without the `mcp` extra. The definition
crosses verbatim (the model gets the server's own schema bytes); the
result is ledger #52's mapping read from the other side: `is_error` is
the verdict, text blocks join, every other block leaves a marker.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from neosian._foundation.llm.base import ToolDefinition
from neosian._foundation.shared.types import ToolFunction, ToolName
from neosian._foundation.tools.base import ToolResult, attach_tool_metadata

PREFIX_SEPARATOR = "__"
_EMPTY_ERROR = "<tool error without content>"

WireCall = Callable[[str, dict[str, Any]], Awaitable[Any]]
"""`(wire_name, arguments) -> CallToolResult` — the connected client's call."""


def bridge_definition(tool: Any, *, prefix: str | None) -> ToolDefinition:
    """The server's declaration as a `ToolDefinition`, schema verbatim."""
    name = tool.name if prefix is None else f"{prefix}{PREFIX_SEPARATOR}{tool.name}"
    return ToolDefinition(
        name=ToolName(name),
        description=tool.description or "",
        parameters=dict(tool.input_schema),
    )


def to_tool_result(result: Any) -> ToolResult[Any]:
    """`CallToolResult` → `ToolResult`.

    Text blocks join by newline; image, audio and resource blocks become
    one-line markers naming type, media type and size — never silently
    dropped (the multimodal rule); `structured_content` is the data when
    the server sent one. `system_reminder` stays an in-process channel.
    """
    text = "\n".join(_render(block) for block in result.content)
    if result.is_error:
        return ToolResult.fail(text or _EMPTY_ERROR)
    structured = result.structured_content
    return ToolResult.ok(text if structured is None else structured)


def _render(block: Any) -> str:
    kind = block.type
    if kind == "text":
        return str(block.text)
    if kind in ("image", "audio"):
        return f"[{kind} {block.mime_type} {len(block.data)} chars base64]"
    if kind == "resource_link":
        size = "?" if block.size is None else block.size
        return f"[resource_link {block.uri} {block.mime_type or '?'} {size} bytes]"
    if kind == "resource":
        resource = block.resource
        mime = resource.mime_type or "?"
        text = getattr(resource, "text", None)
        if text is not None:
            return f"[resource {resource.uri} {mime}]\n{text}"
        return f"[resource {resource.uri} {mime} {len(resource.blob)} chars base64]"
    return f"[{kind}]"


def bridge_tool(
    call: WireCall, wire_name: str, definition: ToolDefinition, *, origin: str
) -> ToolFunction:
    """A tool function forwarding its keyword arguments verbatim to the wire.

    `**arguments` binds anything, so the core's local binding check is
    vacuous by design: the server validates and answers in-band. No
    `functools.wraps` — there is no wrapped signature to expose. `origin`
    names the server in the core's duplicate-name message.
    """

    async def bridged(**arguments: Any) -> ToolResult[Any]:
        return to_tool_result(await call(wire_name, arguments))

    bridged.__name__ = bridged.__qualname__ = str(definition.name)
    bridged.__doc__ = definition.description
    return attach_tool_metadata(bridged, definition, origin=origin)
