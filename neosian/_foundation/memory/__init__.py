"""Memory storage seam (DESIGN §8).

The `MemoryStore` ABC is the host-facing contract (ECOSYSTEM §10);
`FileStore` is the reference implementation; `testing.MemoryStoreContract`
keeps every implementation honest. This package never imports `.testing`
(pytest is not a runtime dependency) and never touches provider internals
(import-linter contract in pyproject).
"""
