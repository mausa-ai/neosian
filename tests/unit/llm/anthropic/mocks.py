"""SDK mock wiring shared by the Anthropic client suites."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, create_autospec

from neosian._foundation.llm.anthropic import AnthropicClient


def sdk(client: AnthropicClient) -> Any:
    """The underlying SDK client, untyped for mock wiring and inspection."""
    return client._client


def stub_stream(client: AnthropicClient, mock_stream: MagicMock) -> None:
    """Stub messages.stream against the installed SDK's real signature, so a
    keyword the SDK dropped turns these suites red (TP-10)."""
    real = sdk(client).messages.stream
    sdk(client).messages.stream = create_autospec(real, return_value=mock_stream)


def mock_complete(client: AnthropicClient, mock_response: MagicMock) -> None:
    """Wire a mocked final message into complete()'s internal-streaming path.

    complete() uses messages.stream() + get_final_message() rather than
    messages.create(), so tests mock the stream context manager and inspect
    sdk(client).messages.stream.call_args.
    """
    inner = MagicMock()
    inner.get_final_message = AsyncMock(return_value=mock_response)
    mock_stream = MagicMock()
    mock_stream.__aenter__ = AsyncMock(return_value=inner)
    mock_stream.__aexit__ = AsyncMock(return_value=False)
    stub_stream(client, mock_stream)
