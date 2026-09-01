"""The one guarded import site for the `server` extra.

Everything the serving side needs from starlette, uvicorn and the MCP
SDK is re-exported here behind one install hint — `wire.py` and
`remote.py` never import this module, so `import neosian` and
`import neosian.server` stay extra-free (pinned by subprocess test).
Absolute imports mean `from mcp.server...` always resolves to the
third-party `mcp` distribution, never to `neosian.mcp`.
"""

from __future__ import annotations

_INSTALL_HINT = (
    "The neosian state process requires the 'server' extra — "
    "uv add 'neosian[server]' (or pip install 'neosian[server]')"
)

try:
    import uvicorn as uvicorn
    from mcp.server.streamable_http_manager import (
        StreamableHTTPASGIApp as StreamableHTTPASGIApp,
        StreamableHTTPSessionManager as StreamableHTTPSessionManager,
    )
    from starlette.applications import Starlette as Starlette
    from starlette.middleware import Middleware as Middleware
    from starlette.requests import Request as Request
    from starlette.responses import JSONResponse as JSONResponse, Response as Response
    from starlette.routing import BaseRoute as BaseRoute, Route as Route
except ImportError as exc:  # pragma: no cover - exercised by subprocess test
    raise ImportError(_INSTALL_HINT) from exc
