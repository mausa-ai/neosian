"""Model-keyed client creation: shipped models route by provider, a
registered model's door names its endpoint and key (DESIGN §19)."""

import os
from typing import Any
from unittest.mock import patch

import pytest

from neosian import Model, OpenAICompatible, Provider, RegisteredModel, register_model
from neosian._foundation.llm.fake import FakeClient
from neosian._foundation.llm.openai import OpenAICompatibleClient
from neosian._foundation.llm.router import _KEYLESS_API_KEY, ProviderRouter
from neosian._foundation.shared.catalog import CEREBRAS
from neosian._foundation.shared.exceptions import MissingAPIKeyError

XAI = OpenAICompatible(
    name="xai", api_key_env="XAI_API_KEY", base_url="https://api.x.ai/v1"
)
LOCAL = OpenAICompatible(
    name="local", api_key_env=None, base_url="http://127.0.0.1:8080/v1"
)


def _grok() -> RegisteredModel:
    return register_model(
        "grok-4", provider=XAI, context_window=131_072, max_output_tokens=16_384
    )


def _gemma() -> RegisteredModel:
    return register_model(
        "gemma-4-e4b-it", provider=LOCAL, context_window=32_768, max_output_tokens=8_192
    )


def _sdk(client: object) -> Any:
    return client._client  # type: ignore[attr-defined]


@pytest.mark.unit
class TestDoorRouting:
    def test_shipped_models_route_by_provider(self) -> None:
        from neosian._foundation.llm.openai import OpenAIClient

        with patch.dict(os.environ, {"OPENAI_API_KEY": "k"}, clear=True):
            client = ProviderRouter().create_client_for(Model.GPT_6_LUNA)
        assert isinstance(client, OpenAIClient)

    def test_the_cerebras_rows_ride_the_shipped_door(self) -> None:
        """Both rows share the door, so the session shares one client (#218)."""
        with patch.dict(os.environ, {"CEREBRAS_API_KEY": "csk"}, clear=True):
            client = ProviderRouter().create_client_for(Model.CEREBRAS_GPT_OSS_120B)
        assert isinstance(client, OpenAICompatibleClient)
        assert client._door is CEREBRAS
        assert Model.CEREBRAS_QWEN_3_8_27B.door is CEREBRAS
        assert _sdk(client).api_key == "csk"
        assert str(_sdk(client).base_url).startswith("https://api.cerebras.ai/v1")
        with pytest.raises(ValueError, match="create_client_for"):
            ProviderRouter().create_client(Provider.CEREBRAS)

    def test_fake_stays_keyless(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert isinstance(
                ProviderRouter().create_client_for(Model.FAKE), FakeClient
            )

    def test_registered_model_gets_the_doors_client(self) -> None:
        grok = _grok()
        with patch.dict(os.environ, {"XAI_API_KEY": "xai-key"}, clear=True):
            client = ProviderRouter(max_retries=1).create_client_for(grok)
        assert isinstance(client, OpenAICompatibleClient)
        assert client._door is XAI
        assert _sdk(client).api_key == "xai-key"
        assert _sdk(client).max_retries == 1
        assert str(_sdk(client).base_url).startswith("https://api.x.ai/v1")

    def test_missing_door_key_is_loud_and_names_it(self) -> None:
        grok = _grok()
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "not-this-one"}, clear=True),
            pytest.raises(MissingAPIKeyError, match="XAI_API_KEY"),
        ):
            ProviderRouter().create_client_for(grok)

    def test_an_explicit_key_needs_no_env(self) -> None:
        grok = _grok()
        with patch.dict(os.environ, {}, clear=True):
            client = ProviderRouter().create_client_for(grok, api_key="explicit")
        assert _sdk(client).api_key == "explicit"

    def test_a_keyless_door_never_sends_the_openai_key(self) -> None:
        """`AsyncOpenAI` reads OPENAI_API_KEY for None and refuses "": the
        placeholder is what a local server sees, never a key (§31.6)."""
        gemma = _gemma()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "not-this-one"}, clear=True):
            client = ProviderRouter().create_client_for(gemma)
        assert isinstance(client, OpenAICompatibleClient)
        assert client._door is LOCAL
        assert _sdk(client).api_key == _KEYLESS_API_KEY
        assert str(_sdk(client).base_url).startswith("http://127.0.0.1:8080/v1")

    def test_an_explicit_key_still_wins_on_a_keyless_door(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            client = ProviderRouter().create_client_for(_gemma(), api_key="explicit")
        assert _sdk(client).api_key == "explicit"

    def test_the_door_row_has_no_client_of_its_own(self) -> None:
        with pytest.raises(ValueError, match="create_client_for"):
            ProviderRouter().create_client(Provider.OPENAI_COMPATIBLE)

    def test_the_door_row_is_never_detected_as_available(self) -> None:
        with patch.dict(os.environ, {"XAI_API_KEY": "k"}, clear=True):
            assert not ProviderRouter().has_provider(Provider.OPENAI_COMPATIBLE)
