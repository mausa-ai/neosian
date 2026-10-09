"""The shipped turn-store conformance kit (DESIGN §9.7). Import as
`neosian.conversation.testing` from a test suite — requires pytest and
pytest-asyncio, which neosian does not depend on at runtime. The
`ErasureContract` slice (§38) is opt-in: a store that implements
`Erasable` subclasses it beside the kit.
"""

from neosian._foundation.conversation.testing import ConversationStoreContract
from neosian._foundation.conversation.testing_erasure import ErasureContract
from neosian._foundation.conversation.testing_search import SearchContract

__all__ = ["ConversationStoreContract", "ErasureContract", "SearchContract"]
