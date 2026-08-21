"""Store-truth scoring (DESIGN §13.12).

Every assertion is an on-disk truth claim: the caller hands a
MemoryConfig over a freshly constructed store, so a pass means the
documents survive re-reading, not that an in-memory view agreed.
Failures are `store: `-prefixed and name the offenders (§13.4).
"""

from neosian._foundation.evaluation.matcher import match_text
from neosian._foundation.evaluation.memory_types import (
    DocumentExpectation,
    StoreExpectation,
)
from neosian._foundation.memory.mounts import MemoryConfig, resolve

# `versions()` clips to its `limit` — far above any eval scenario's count.
_VERSION_LIMIT = 1000


async def check_store(
    config: MemoryConfig, expect: StoreExpectation
) -> tuple[str, ...]:
    """Check one session's `expect_store:` block against the store."""
    failures: list[str] = []
    for document in expect.documents:
        failures.extend(await _check_document(config, document))
    for prefix, count in expect.counts.items():
        failures.extend(await _check_count(config, prefix, count))
    for path in expect.absent:
        mount, rest = resolve(config, path)
        found = await config.store.read(mount.scope, rest)
        if found is not None:
            failures.append(
                f"store: expected no document at {path}, found v{found.version}"
            )
    for text in expect.forbidden:
        failures.extend(await _check_forbidden(config, text))
    return tuple(failures)


async def _check_document(
    config: MemoryConfig, expected: DocumentExpectation
) -> list[str]:
    mount, rest = resolve(config, expected.path)
    document = await config.store.read(mount.scope, rest)
    if document is None:
        return [f"store: no document at {expected.path}"]
    failures = [
        f"store: {reason}"
        for matcher in expected.content
        if (
            reason := match_text(
                matcher, document.content, label=f"document '{expected.path}'"
            )
        )
        is not None
    ]
    if expected.versions is None and expected.actions is None:
        return failures

    rows = await config.store.versions(mount.scope, rest, limit=_VERSION_LIMIT)
    actions = tuple(row.action for row in reversed(rows))  # oldest-first
    if expected.versions is not None and len(rows) != expected.versions:
        failures.append(
            f"store: {expected.path}: expected {expected.versions} version "
            f"rows, got {len(rows)} (actions: {', '.join(actions)})"
        )
    if expected.actions is not None and actions != expected.actions:
        failures.append(
            f"store: {expected.path}: expected actions "
            f"{', '.join(expected.actions)}, got {', '.join(actions)}"
        )
    return failures


async def _check_count(config: MemoryConfig, prefix: str, count: int) -> list[str]:
    mount, rest = resolve(config, prefix)
    entries = await config.store.list_documents(mount.scope, prefix=rest)
    if len(entries) == count:
        return []
    listing = ", ".join(entry.path for entry in entries) or "none"
    return [
        f"store: expected {count} document(s) under {prefix}, "
        f"got {len(entries)} ({listing})"
    ]


async def _check_forbidden(config: MemoryConfig, text: str) -> list[str]:
    failures: list[str] = []
    for mount in config.mounts:
        for entry in await config.store.list_documents(mount.scope):
            document = await config.store.read(mount.scope, entry.path)
            if document is not None and text in document.content:
                failures.append(
                    f"store: forbidden text {text!r} found in "
                    f"/{mount.mount_path}/{entry.path}"
                )
    return failures
