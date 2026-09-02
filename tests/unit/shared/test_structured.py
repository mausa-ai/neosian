"""`structured_call` carries its degrade — the fourth element names the
failure — and lets a `ConfigurationError` through: the caller's own
setup, never the model's reply (the #84 rule)."""

import logging

import pytest
from pydantic import BaseModel

from neosian import Model
from neosian._foundation.llm.base import BaseLLMClient, Usage
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.exceptions import ConfigurationError
from neosian._foundation.shared.structured import structured_call
from neosian._foundation.shared.types import AnyModel

_USAGE = Usage(input_tokens=10, output_tokens=2)


class _Reply(BaseModel):
    answer: str


def _scripted(content: str) -> FakeClient:
    return FakeClient(FakeScript(turns=(FakeTurn(content=content, usage=_USAGE),)))


@pytest.mark.unit
class TestStructuredCall:
    async def test_success_carries_no_degrade(self) -> None:
        fake = _scripted(_Reply(answer="42").model_dump_json())
        parsed, usage, model, degraded = await structured_call(
            lambda _: fake, Model.FAKE, "system", "payload", _Reply, "Probe"
        )
        assert parsed == _Reply(answer="42")
        assert usage == _USAGE
        assert model is not None
        assert degraded is None

    async def test_a_malformed_reply_degrades_with_the_reason(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        fake = _scripted("not json")
        with caplog.at_level(logging.WARNING):
            parsed, usage, model, degraded = await structured_call(
                lambda _: fake, Model.FAKE, "system", "payload", _Reply, "Probe"
            )
        assert (parsed, usage, model) == (None, None, None)
        assert degraded is not None and degraded.startswith("Probe failed: ")
        assert "Probe failed; degrading" in caplog.text

    async def test_a_configuration_error_propagates(self) -> None:
        def acquire(_: AnyModel) -> BaseLLMClient:
            raise ConfigurationError("no key for this provider")

        with pytest.raises(ConfigurationError, match="no key"):
            await structured_call(
                acquire, Model.FAKE, "system", "payload", _Reply, "Probe"
            )
