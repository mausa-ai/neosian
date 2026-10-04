"""Chat's local servers (ledger #330): a llama-server and an Ollama on a
fake transport — what registers, at which window, what counts as running,
and silence when nothing answers."""

from collections.abc import Mapping
from typing import Any

import httpx
import pytest

import neosian._cli.local_servers as local_servers
from neosian._cli.local_servers import discover, local_urls
from neosian._foundation.llm.base import Usage
from neosian._foundation.shared.catalog import OpenAICompatible
from neosian._foundation.shared.registry import (
    lookup_model,
    provider_label,
    register_model,
    registered_models,
)

LLAMA = "http://127.0.0.1:8080/v1"
OLLAMA = "http://127.0.0.1:11434/v1"
_QWEN = "lmstudio-community/Qwen3.5-4B-GGUF"


def _llama(model_id: str = _QWEN, n_ctx: int = 8192) -> dict[str, Any]:
    return {
        "/v1/models": {"object": "list", "data": [{"id": model_id}]},
        "/props": {"default_generation_settings": {"n_ctx": n_ctx}},
    }


def _ollama(installed: tuple[str, ...], loaded: Mapping[str, int]) -> dict[str, Any]:
    return {
        "/v1/models": {"object": "list", "data": [{"id": i} for i in installed]},
        "/api/ps": {
            "models": [
                {"name": name, "model": name, "context_length": window}
                for name, window in loaded.items()
            ]
        },
    }


def _servers(**hosts: dict[str, Any]) -> httpx.MockTransport:
    """Ports by name (`p8080=…`): an unnamed port refuses, an unknown path 404s."""

    def handler(request: httpx.Request) -> httpx.Response:
        routes = hosts.get(f"p{request.url.port}")
        if routes is None:
            raise httpx.ConnectError("refused", request=request)
        body = routes.get(request.url.path)
        if body is None:
            return httpx.Response(404)
        if isinstance(body, str):
            return httpx.Response(200, text=body)
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


class TestDiscover:
    async def test_a_llama_server_runs_its_model_at_its_window(self) -> None:
        (model,) = await discover([LLAMA], transport=_servers(p8080=_llama()))
        assert model.value == _QWEN and lookup_model(_QWEN) is model
        assert model.context_window == 8192 and model.max_output_tokens == 8192
        assert provider_label(model) == "llama-cpp"
        assert model.door.api_key_env is None
        assert model.door.base_url == LLAMA
        assert Usage(input_tokens=1000, output_tokens=1000).cost_micro_usd(model) == 0

    async def test_ollama_offers_every_installed_model_and_runs_the_loaded(
        self,
    ) -> None:
        ollama = _ollama(("gemma4:e4b", "qwen3:8b"), {"gemma4:e4b": 32768})
        running = await discover([OLLAMA], transport=_servers(p11434=ollama))
        assert [m.value for m in running] == ["gemma4:e4b"]
        installed = lookup_model("qwen3:8b")
        assert installed is not None and installed in registered_models()
        assert (running[0].context_window, installed.context_window) == (32768, 4096)
        assert provider_label(running[0]) == "ollama"

    async def test_running_models_come_in_url_order(self) -> None:
        transport = _servers(
            p11434=_ollama(("gemma4:e4b",), {"gemma4:e4b": 8192}), p8080=_llama()
        )
        running = await discover([OLLAMA, LLAMA], transport=transport)
        assert [m.value for m in running] == ["gemma4:e4b", _QWEN]

    @pytest.mark.parametrize(
        "routes",
        [
            None,  # the port refuses
            {},  # every path 404s
            {"/v1/models": "not json", "/props": {}},
            {"/v1/models": {"data": "nope"}, "/props": {}},
            {"/v1/models": {"data": [{"id": "x"}]}, "/props": {"n_ctx": 1}},
            {"/v1/models": {"data": [{"id": "x"}]}},  # neither server: no window
        ],
    )
    async def test_anything_else_is_silence(
        self, routes: dict[str, Any] | None
    ) -> None:
        hosts = {} if routes is None else {"p8080": routes}
        before = registered_models()
        assert await discover([LLAMA], transport=_servers(**hosts)) == ()
        assert registered_models() == before

    async def test_an_id_taken_elsewhere_stands(self) -> None:
        door = OpenAICompatible(
            name="local", api_key_env=None, base_url="http://127.0.0.1:8081/v1"
        )
        theirs = register_model(
            _QWEN, provider=door, context_window=32768, max_output_tokens=8192
        )
        assert await discover([LLAMA], transport=_servers(p8080=_llama())) == ()
        assert lookup_model(_QWEN) is theirs
        shipped = _servers(p8080=_llama("gpt-oss-120b"))
        assert await discover([LLAMA], transport=shipped) == ()

    async def test_probing_again_keeps_the_first_window(self) -> None:
        (first,) = await discover([LLAMA], transport=_servers(p8080=_llama()))
        again = await discover([LLAMA], transport=_servers(p8080=_llama()))
        assert again == (first,)
        restarted = _servers(p8080=_llama(n_ctx=32768))
        assert await discover([LLAMA], transport=restarted) == ()
        assert lookup_model(_QWEN) is first and first.context_window == 8192


class TestTheUrls:
    def test_the_configured_come_first_then_the_defaults_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(local_servers, "DEFAULT_URLS", (LLAMA, OLLAMA))
        extra = ["http://127.0.0.1:8081/v1/", LLAMA]
        assert local_urls({"local": extra}) == (
            "http://127.0.0.1:8081/v1",
            LLAMA,
            OLLAMA,
        )
        assert local_urls({}) == (LLAMA, OLLAMA)

    @pytest.mark.parametrize(
        "value", ["http://127.0.0.1:8081/v1", [8081], ["127.0.0.1:8081"]]
    )
    def test_anything_but_a_list_of_urls_is_grammar(self, value: object) -> None:
        with pytest.raises(ValueError, match=r"\[chat\] local"):
            local_urls({"local": value})
