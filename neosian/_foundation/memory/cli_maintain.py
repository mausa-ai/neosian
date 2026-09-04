"""The `maintain` verb's execution tier (DESIGN §16, §14.2).

The gardener on the shell: keyless by default, `--model` adds the
semantic pass through an injected client factory — the storage contract
denies this package the router, so the entry tiers supply a lazy
router-backed factory that checks the model's key eagerly (the #84
rule). The only CLI module that touches `llm.base`.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import timedelta
from typing import TYPE_CHECKING, TextIO

from neosian._foundation.memory.maintenance import MaintenanceResult, run_maintenance
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.memory.store_lifetime import open_store
from neosian._foundation.shared.exceptions import NeosianError
from neosian._foundation.shared.types import AnyModel, ClientFactory, format_micro_usd

if TYPE_CHECKING:
    from collections.abc import Callable

    from neosian._foundation.llm.base import BaseLLMClient
    from neosian._foundation.memory.base import MemoryStore
    from neosian._foundation.memory.cli_grammar import Request


async def execute_maintain(
    request: Request,
    client_factory: ClientFactory | None,
    *,
    out: TextIO,
    err: TextIO,
) -> int:
    client: BaseLLMClient | None = None
    acquire: Callable[[AnyModel], BaseLLMClient] | None = None
    if request.model is not None:
        if client_factory is None:
            err.write("error: --model is not available from this entry point\n")
            return 2
        try:
            # Eager, so a missing provider key is loud at construction
            # (the #84 rule) instead of degrading inside the model stage.
            client = client_factory(request.model)
        except NeosianError as exc:
            err.write(f"error: [{exc.code}] {exc.message}\n")
            return 2
        acquire = _lease(client)
    try:
        async with open_store(request.settings) as store:
            result = await _maintain_on(store, request, acquire)
    finally:
        if client is not None:
            await client.close()
    return _render_maintenance(result, request, out=out, err=err)


def _lease(client: BaseLLMClient) -> Callable[[AnyModel], BaseLLMClient]:
    """The one constructed client as an acquire lease — the caller
    (`execute_maintain`) owns its lifetime and closes it."""

    def acquire(_: AnyModel) -> BaseLLMClient:
        return client

    return acquire


async def _maintain_on(
    store: MemoryStore,
    request: Request,
    acquire: Callable[[AnyModel], BaseLLMClient] | None,
) -> MaintenanceResult:
    config = MemoryConfig(store=store, mounts=request.settings.mounts)
    return await run_maintenance(
        config,
        acquire=acquire,
        model=request.model,
        actor=request.settings.actor,
        min_age=timedelta(days=request.min_age_days),
    )


def _render_maintenance(
    result: MaintenanceResult, request: Request, *, out: TextIO, err: TextIO
) -> int:
    cost = (
        result.usage.cost_micro_usd(request.model)
        if result.usage is not None and request.model is not None
        else None
    )
    if request.json_output:
        envelope = {
            "writes": [asdict(write) for write in result.writes],
            "model": result.model,
            "usage": None if result.usage is None else asdict(result.usage),
            "cost_micro_usd": cost,
            "degraded": result.degraded,
        }
        out.write(json.dumps(envelope) + "\n")
    else:
        for write in result.writes:
            suffix = "" if write.version is None else f" (v{write.version})"
            out.write(f"{write.command} {write.path}{suffix}\n")
        if not result.writes:
            out.write("nothing to do\n")
        if result.usage is not None:
            spend = "unknown" if cost is None else format_micro_usd(cost)
            out.write(
                f"model pass: {result.model},"
                f" {result.usage.total_tokens} tokens, {spend}\n"
            )
    if result.degraded is not None:
        # The stage was asked for and degraded — the explicit shell tells
        # the operator why, unlike the library's close paths (§16).
        err.write(
            f"error: the model stage failed ({result.degraded});"
            " deterministic actions still landed\n"
        )
        return 1
    return 0
