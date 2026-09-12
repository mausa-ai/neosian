"""A mock ProviderRouter shared by the Agent suites."""

from unittest.mock import AsyncMock, MagicMock

from neosian._foundation.llm.base import BaseLLMClient


def create_mock_router(mock_client: BaseLLMClient | None = None) -> MagicMock:
    """Create a mock ProviderRouter that returns the given client.

    Args:
        mock_client: The mock client to return. If None, creates a new AsyncMock.

    Returns:
        MagicMock configured as a ProviderRouter.
    """
    if mock_client is None:
        mock_client = AsyncMock(spec=BaseLLMClient)

    mock_router = MagicMock()
    mock_router.has_provider.return_value = True
    mock_router.create_client_for.return_value = mock_client
    return mock_router
