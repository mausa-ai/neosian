"""The shipped turn-store conformance kit (DESIGN §9.7). Import as
`neosian.conversation.testing` from a test suite — requires pytest and
pytest-asyncio, which neosian does not depend on at runtime.
"""

from neosian._foundation.conversation.testing import ConversationStoreContract
from neosian._foundation.conversation.testing_search import SearchContract

__all__ = ["ConversationStoreContract", "SearchContract"]
