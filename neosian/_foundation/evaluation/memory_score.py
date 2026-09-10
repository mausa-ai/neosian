"""Store-truth scoring (DESIGN §13.12).

Every assertion is an on-disk truth claim: the caller hands a
MemoryConfig over a freshly constructed store, so a pass means the
documents survive re-reading, not that an in-memory view agreed.
Failures are `store: `-prefixed and name the offenders (§13.4).
"""

from neosian._foundation.evaluation.matcher import clip, match_text
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
    if expected.path_prefix is not None:
        return await _check_prefix(config, expected)
    assert expected.path is not None  # __post_init__ guarantees exactly one
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
    return failures + await _check_history(config, expected, expected.path)


async def _check_prefix(
    config: MemoryConfig, expected: DocumentExpectation
) -> list[str]:
    """Exactly one live document under the prefix satisfies `content` —
    naming is the model's choice, so the pin is region + fact, and two
    matching documents is the duplicate the dedup discipline forbids."""
    prefix = expected.path_prefix
    assert prefix is not None
    described = " and ".join(m.describe() for m in expected.content) or "any content"
    mount, rest = resolve(config, prefix)
    entries = await config.store.list_documents(mount.scope, prefix=rest)
    matches: list[str] = []
    live: list[str] = []
    for entry in entries:
        document = await config.store.read(mount.scope, entry.path)
        if document is None:
            continue
        path = f"/{mount.mount_path}/{entry.path}"
        # The opening bytes ride the failure line: a red cell's store is
        # gone by the time a CI log is read, so the log must carry them.
        live.append(f"{path}: {clip(document.content)!r}")
        if all(
            match_text(matcher, document.content, label="") is None
            for matcher in expected.content
        ):
            matches.append(path)
    if not matches:
        return [
            f"store: no document under {prefix} matching {described} "
            f"(live: {', '.join(live) or 'none'})"
        ]
    if len(matches) > 1:
        return [
            f"store: {len(matches)} documents under {prefix} match {described} "
            f"({', '.join(matches)}) — expected exactly one"
        ]
    return await _check_history(config, expected, matches[0])


async def _check_history(
    config: MemoryConfig, expected: DocumentExpectation, path: str
) -> list[str]:
    """Apply the `versions`/`actions` pins to one resolved document."""
    if expected.versions is None and expected.actions is None:
        return []
    mount, rest = resolve(config, path)
    failures: list[str] = []
    rows = await config.store.versions(mount.scope, rest, limit=_VERSION_LIMIT)
    actions = tuple(row.action for row in reversed(rows))  # oldest-first
    if expected.versions is not None and len(rows) != expected.versions:
        failures.append(
            f"store: {path}: expected {expected.versions} version "
            f"rows, got {len(rows)} (actions: {', '.join(actions)})"
        )
    if expected.actions is not None and actions != expected.actions:
        failures.append(
            f"store: {path}: expected actions "
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
