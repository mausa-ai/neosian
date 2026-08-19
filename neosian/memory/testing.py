"""The shipped store-conformance kit (ECOSYSTEM §10). Import as
`neosian.memory.testing` from a test suite — requires pytest and
pytest-asyncio, which neosian does not depend on at runtime.
"""

from neosian._foundation.memory.testing import MemoryStoreContract

__all__ = ["MemoryStoreContract"]
