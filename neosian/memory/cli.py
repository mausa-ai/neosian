"""The `neosian memory` entry point — the shell transport's run tier.

Library code stays async-only (`asyncio.run` deadlocks in notebooks and
servers); this module is the CLI tier, like `neosian/mcp/serve.py`. The
engine — grammar, store lifetime, rendering — is
`_foundation/memory/cli.py`; the eval harness calls that async engine
in-process from its running loop (DESIGN §14), while this wrapper owns
the loop, the real streams and interrupt handling.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import TYPE_CHECKING

from neosian._foundation.memory.cli import run

if TYPE_CHECKING:
    from neosian._foundation.llm.base import BaseLLMClient
    from neosian._foundation.shared.types import AnyModel


def _client_factory(model: AnyModel) -> BaseLLMClient:
    """The router-backed factory for `maintain --model` — imported lazily
    so every keyless command stays provider-SDK-free; a missing key
    raises `MissingAPIKeyError` here, loud at construction (§16)."""
    from neosian._foundation.agent.guards import require_model_key
    from neosian._foundation.llm.router import ProviderRouter

    require_model_key(model)
    return ProviderRouter().create_client_for(model)


def main(argv: list[str] | None = None, *, prog: str = "neosian memory") -> int:
    try:
        return asyncio.run(
            run(
                sys.argv[1:] if argv is None else argv,
                os.environ,
                stdin=sys.stdin,
                out=sys.stdout,
                err=sys.stderr,
                prog=prog,
                client_factory=_client_factory,
            )
        )
    except ImportError as exc:
        # The reinstall hint, not a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
