"""The shipped store-conformance kit (ECOSYSTEM §10). Import as
`neosian.memory.testing` from a test suite — requires pytest and
pytest-asyncio, which neosian does not depend on at runtime.

`MemoryStoreContract` is the whole kit; the two slices beside it are
exported so a host can name what it inherits (NQ2, TP-16) — they carry
the ledger reads (`LedgerContract`) and `expected_version` plus C2
read-your-writes (`ConcurrencyContract`).
"""

from neosian._foundation.memory.testing import MemoryStoreContract
from neosian._foundation.memory.testing_audit import LedgerContract
from neosian._foundation.memory.testing_concurrency import ConcurrencyContract

__all__ = ["ConcurrencyContract", "LedgerContract", "MemoryStoreContract"]
