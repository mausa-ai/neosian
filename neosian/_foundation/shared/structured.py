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
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel

from neosian._foundation.llm.base import Message, Role, text_of
from neosian._foundation.shared.exceptions import ConfigurationError
from neosian._foundation.shared.schema import validate_json
from neosian._foundation.shared.types import AnyModel, ResponseFormat

if TYPE_CHECKING:
    from collections.abc import Callable

    from neosian._foundation.llm.base import BaseLLMClient, Usage

logger = logging.getLogger(__name__)


async def structured_call[T: BaseModel](
    acquire: Callable[[AnyModel], BaseLLMClient],
    model: AnyModel,
    system: str,
    payload: str,
    schema: type[T],
    what: str,
) -> tuple[T | None, Usage | None, str | None, str | None]:
    """One structured-output call through the lease: `(parsed, usage,
    model, degraded)` — `degraded` is None on success and names the
    failure when `parsed` is None."""
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
        return parsed, response.usage, response.model, None
    except ConfigurationError:
        raise
    except Exception as exc:
        logger.warning("%s failed; degrading", what, exc_info=True)
        return None, None, None, f"{what} failed: {type(exc).__name__}: {exc}"
