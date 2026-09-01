"""The `neosian memory` engine — one async `run` over injected streams.

The shell is a transport: the six commands ride the shared dispatcher
(`dispatch.py`), the store flags ride the shared grammar (`settings.py`),
and `--json` prints the function tool's `ToolResult` envelope verbatim
(ledger #77) — so the CLI cannot drift from the other transports. The
`maintain` verb (DESIGN §16) and the operator verbs `versions` /
`redact` / `revert` (§14.2, NP) are not dispatch commands: they print
their own envelopes. The grammar lives in `cli_grammar.py`, the verb
execution tiers in `cli_maintain.py` / `cli_operate.py`, store lifetime
in `store_lifetime.py`. The engine is async and stream-injected: the
entry tier (`neosian/memory/cli.py`) wraps it in `asyncio.run` over real
streams, while the eval harness calls it in-process from a running loop.

Exit tiering (DESIGN §14.1): 0 success · 1 the command ran and failed
(a corrective dispatch failure, rendered `error:`/`hint:`) · 2 the argv
was wrong (grammar, unknown command, bad scope or mount — nothing is
constructed) · 130 interrupt (entry tier).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, TextIO

# ARGUMENT_KEYS re-exported for the eval cli transport (the settings.py
# re-export idiom); the grammar itself lives in cli_grammar.py.
from neosian._foundation.memory.cli_grammar import (
    ARGUMENT_KEYS as ARGUMENT_KEYS,
    OPERATOR_VERBS,
    Request,
    parse_request,
)
from neosian._foundation.memory.cli_maintain import execute_maintain
from neosian._foundation.memory.cli_operate import execute_operator
from neosian._foundation.memory.dispatch import dispatch
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.memory.store_lifetime import open_store
from neosian._foundation.shared.exceptions import MemoryStoreError
from neosian._foundation.tools.base import ToolResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from neosian._foundation.llm.base import BaseLLMClient
    from neosian._foundation.memory.base import MemoryStore
    from neosian._foundation.shared.types import Provider


async def _execute(request: Request) -> ToolResult[str]:
    async with open_store(request.settings) as store:
        return await _dispatch_on(store, request)


async def _dispatch_on(store: MemoryStore, request: Request) -> ToolResult[str]:
    config = MemoryConfig(store=store, mounts=request.settings.mounts)
    return await dispatch(
        config,
        request.command,
        request.arguments,
        actor=request.settings.actor,
    )


def _render(
    result: ToolResult[str], *, json_output: bool, out: TextIO, err: TextIO
) -> int:
    if json_output:
        out.write(result.to_json() + "\n")
    elif result.success:
        if result.data:
            out.write(result.data if result.data.endswith("\n") else result.data + "\n")
        if result.system_reminder:
            err.write(f"hint: {result.system_reminder}\n")
    else:
        err.write(f"error: {result.error}\n")
        if result.system_reminder:
            err.write(f"hint: {result.system_reminder}\n")
    return 0 if result.success else 1


async def run(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    stdin: TextIO,
    out: TextIO,
    err: TextIO,
    prog: str = "neosian memory",
    client_factory: Callable[[Provider], BaseLLMClient] | None = None,
) -> int:
    """Parse and execute one memory command; construct nothing on exit 2.

    `client_factory` powers `maintain --model` only — the entry tiers
    supply the real router-backed one; this package may not construct
    provider clients itself (the storage import contract, DESIGN §1)."""
    try:
        request = parse_request(argv, env, stdin=stdin, out=out, err=err, prog=prog)
    except SystemExit as exc:  # argparse: usage already on the streams
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 2
    except MemoryStoreError as exc:  # Mount() scope/path validation
        err.write(f"error: [{exc.code}] {exc.message}\n")
        return 2
    if request.command == "maintain":
        return await execute_maintain(request, client_factory, out=out, err=err)
    if request.command in OPERATOR_VERBS:
        return await execute_operator(request, out=out, err=err)
    result = await _execute(request)
    return _render(result, json_output=request.json_output, out=out, err=err)
