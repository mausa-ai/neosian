"""The local servers chat finds by itself (ledger #330): a llama-server or
an Ollama on this machine, probed on loopback when chat resolves its
model and when `/model` opens its picker.

An answering server's models are registered on a keyless door named for
the server (`neosian docs local`), each at the window it serves —
llama-server's `n_ctx`, a loaded Ollama model's `context_length` — and
priced at zero. A model running now (llama-server's, an Ollama model in
memory) can be chat's default; an installed Ollama model is offered by
the picker at Ollama's default window until it is loaded. Silent when
nothing answers: a refused port costs nothing, a hung one the timeout.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any, Final

import httpx

from neosian._foundation.shared.catalog import OpenAICompatible
from neosian._foundation.shared.constants import LLMDefaults
from neosian._foundation.shared.exceptions import ConfigurationError
from neosian._foundation.shared.model_spec import ModelPricing
from neosian._foundation.shared.registry import RegisteredModel, register_model

DEFAULT_URLS: Final = ("http://127.0.0.1:8080/v1", "http://127.0.0.1:11434/v1")
TIMEOUT_SECONDS: Final = 0.5
_OLLAMA_WINDOW: Final = 4096  # what Ollama serves a model it has not loaded
_FREE: Final = ModelPricing(input_per_mtok=0, output_per_mtok=0)
_UNREADABLE: Final = (httpx.HTTPError, ValueError, KeyError, TypeError)

type Served = tuple[
    str, list[tuple[str, int, bool]]
]  # door name, (id, window, running)


def local_urls(chat: Mapping[str, Any]) -> tuple[str, ...]:
    """`[chat] local`'s base URLs, then the two default ports, each once."""
    extra = chat.get("local", [])
    if not isinstance(extra, list) or not all(
        isinstance(url, str) and url.startswith(("http://", "https://"))
        for url in extra
    ):
        raise ValueError("[chat] local must be a list of http(s) base URLs")
    return tuple(dict.fromkeys(url.rstrip("/") for url in [*extra, *DEFAULT_URLS]))


async def discover(
    urls: Sequence[str], *, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[RegisteredModel, ...]:
    """Register what the servers at `urls` serve; the running models, in
    order. An id the catalog ships or something else registered stands."""
    async with httpx.AsyncClient(
        transport=transport, timeout=TIMEOUT_SECONDS
    ) as client:
        found = await asyncio.gather(*(_served(client, url) for url in urls))
    running: list[RegisteredModel] = []
    for url, (name, rows) in zip(urls, found, strict=True):
        if not rows:
            continue
        door = OpenAICompatible(  # the measured local recipe (`neosian docs local`)
            name=name,
            api_key_env=None,
            base_url=url,
            reasoning_effort=False,
            reasoning_field="reasoning_content",
        )
        for model_id, window, live in rows:
            try:
                model = register_model(
                    model_id,
                    provider=door,
                    context_window=window,
                    max_output_tokens=LLMDefaults.MAX_OUTPUT_TOKENS,
                    pricing=_FREE,
                )
            except ConfigurationError:
                continue
            if live:
                running.append(model)
    return tuple(running)


async def _served(client: httpx.AsyncClient, url: str) -> Served:
    """One server's models: llama-server answers `/props`, Ollama
    `/api/ps`; anything else, or nothing, is no models."""
    try:
        listed = await _json(client, f"{url}/models")
        ids = [str(row["id"]) for row in listed["data"]]
        root = url.removesuffix("/v1")
        replies: tuple[Any, Any] = await asyncio.gather(
            _json(client, f"{root}/props"),
            _json(client, f"{root}/api/ps"),
            return_exceptions=True,
        )
        props, ps = replies
        if isinstance(props, dict):
            window = int(props["default_generation_settings"]["n_ctx"])
            return "llama-cpp", [(model_id, window, True) for model_id in ids]
        if isinstance(ps, dict):
            loaded = {
                row[key]: int(row.get("context_length") or _OLLAMA_WINDOW)
                for row in ps["models"]
                for key in ("name", "model")
            }
            return "ollama", [
                (model_id, loaded.get(model_id, _OLLAMA_WINDOW), model_id in loaded)
                for model_id in ids
            ]
    except _UNREADABLE:
        pass
    return "", []


async def _json(client: httpx.AsyncClient, url: str) -> Any:
    response = await client.get(url)
    response.raise_for_status()
    return response.json()
