"""Public server surface (DESIGN §18). Import as `neosian.server`.

One name: `build_app` — the state process's ASGI app for embedders who
mount it on their own uvicorn/hypercorn. The import is lazy so
`import neosian.server` stays extra-free (pinned by subprocess test);
touching `build_app` without the `server` extra raises the install hint.
The wire's client half, `RemoteStore`, lives on the root package and the
memory/conversation facades — it needs no extra.
"""

from typing import Any

__all__ = ["build_app"]


def __getattr__(name: str) -> Any:
    if name == "build_app":
        from neosian._foundation.server.app import build_app

        return build_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
