"""One degrade-safe structured-output call through an ``acquire`` lease.

Shared by compaction's two distillation batches (§9.6), reflection (§15)
and memory maintenance (§16). ``acquire`` is a lease: the caller owns the
client's lifetime (ledger #33 — closing a cached client here would leave
a dead handle in the caller's pool). Any failure degrades — ``None``
result, warning logged — so no caller ever loses its own work to a
distillation call. Lives in the shared kernel because both the
conversation and memory layers call it and neither may import the other's
internals gratuitously (DESIGN §1). The degrade is *carried*: the fourth
element names the reason, and a `ConfigurationError` — the caller's own
setup, never the model's reply — propagates (the #84 rule).

Every call is billed, degraded or not, so each one is *reported* (N7,
ledger #332): ``report`` receives a `StructuredCallReport` the caller turns
into its `on_llm_call` event; this module knows no hooks, so the storage
seam modules that thread it import no agent.
"""

from __future__ import annotations

import inspect
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel

from neosian._foundation.llm.base import Message, Role, text_of
from neosian._foundation.shared.exceptions import ConfigurationError
from neosian._foundation.shared.schema import validate_json
from neosian._foundation.shared.types import AnyModel, ResponseFormat

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from neosian._foundation.llm.base import BaseLLMClient, CompletionResponse, Usage
    from neosian._foundation.shared.types import Provider

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StructuredCallReport:
    """What one structured call was and cost: the fields an `LlmCallEvent`
    needs. `usage` and `model` are the response's when one arrived, even
    if its reply then failed validation — billed is billed."""

    requested_model: str
    provider: Provider
    model: str | None
    usage: Usage | None
    started_at: datetime
    duration_ms: int
    error_code: str | None


type Report = Callable[[StructuredCallReport], Awaitable[None] | None]


async def structured_call[T: BaseModel](
    acquire: Callable[[AnyModel], BaseLLMClient],
    model: AnyModel,
    system: str,
    payload: str,
    schema: type[T],
    what: str,
    *,
    report: Report | None = None,
) -> tuple[T | None, Usage | None, str | None, str | None]:
    """One structured-output call through the lease: `(parsed, usage,
    model, degraded)` — `degraded` is None on success and names the
    failure when `parsed` is None."""
    started = time.monotonic()
    started_at = datetime.now(UTC)
    response: CompletionResponse | None = None
    try:
        client = acquire(model)
        response = await client.complete(
            [
                Message(role=Role.SYSTEM, content=system),
                Message(role=Role.USER, content=payload),
            ],
            model=model,
            response_format=ResponseFormat(schema=schema),
            cache_conversation=False,
            # A one-shot distillation call never wants provider-side
            # history compaction, whatever the sending agent opted into.
            server_compaction=False,
        )
        parsed = validate_json(schema, text_of(response.message))
        assert isinstance(parsed, schema)
    except ConfigurationError:
        raise
    except Exception as exc:
        # The detail goes to the log; the carried reason names the failure
        # by type only — a validation error quotes the model's reply, which
        # was built from memory bodies, and this string is result surface.
        logger.warning("%s failed; degrading", what, exc_info=True)
        await _report(report, model, response, started, started_at, error=exc)
        return None, None, None, f"{what} failed: {type(exc).__name__}"
    await _report(report, model, response, started, started_at)
    return parsed, response.usage, response.model, None


async def _report(
    report: Report | None,
    model: AnyModel,
    response: CompletionResponse | None,
    started: float,
    started_at: datetime,
    *,
    error: BaseException | None = None,
) -> None:
    if report is None:
        return
    code = getattr(error, "code", None) if error is not None else None
    outcome = report(
        StructuredCallReport(
            requested_model=model.value,
            provider=model.provider,
            model=None if response is None else response.model,
            usage=None if response is None else response.usage,
            started_at=started_at,
            duration_ms=int((time.monotonic() - started) * 1000),
            error_code=code if isinstance(code, str) else None,
        )
    )
    if inspect.isawaitable(outcome):
        await outcome
