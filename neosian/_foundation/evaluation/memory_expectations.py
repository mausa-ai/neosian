"""`expect_store:` block parsing (DESIGN §13.12).

The store-truth half of a memory session, split from the suite loader
the way `expectations.py` is split from `loader.py`. Strict keys; every
path must name a declared mount at load time, so a typo fails the
config, never a run.
"""

from typing import Any

from neosian._foundation.evaluation.expectations import parse_response
from neosian._foundation.evaluation.memory_types import (
    DocumentExpectation,
    StoreExpectation,
)
from neosian._foundation.memory.types import MemoryAction
from neosian._foundation.shared.exceptions import EvalCaseInvalidError

_STORE_KEYS = frozenset({"documents", "counts", "absent", "forbidden"})
_DOCUMENT_KEYS = frozenset({"path", "path_prefix", "content", "versions", "actions"})
_ACTIONS: tuple[MemoryAction, ...] = ("created", "modified", "deleted")


def parse_store_expectation(
    data: Any, label: str, mount_paths: frozenset[str]
) -> StoreExpectation:
    if data is None:
        return StoreExpectation()
    if not isinstance(data, dict) or not data:
        raise EvalCaseInvalidError(label, "'expect_store' must be a non-empty mapping")
    for key in data:
        if key not in _STORE_KEYS:
            raise EvalCaseInvalidError(label, f"expect_store: unknown key '{key}'")

    documents_data = data.get("documents")
    documents: list[DocumentExpectation] = []
    if documents_data is not None:
        if not isinstance(documents_data, list) or not documents_data:
            raise EvalCaseInvalidError(
                label, "expect_store: 'documents' must be a non-empty list"
            )
        documents = [
            _parse_document(entry, label, mount_paths) for entry in documents_data
        ]

    counts: dict[str, int] = {}
    counts_data = data.get("counts")
    if counts_data is not None:
        if not isinstance(counts_data, dict) or not counts_data:
            raise EvalCaseInvalidError(
                label, "expect_store: 'counts' must be a non-empty mapping"
            )
        for prefix, count in counts_data.items():
            prefix = str(prefix)
            _require_mounted(prefix, label, mount_paths, allow_root=True)
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise EvalCaseInvalidError(
                    label,
                    f"expect_store: count for '{prefix}' must be a "
                    "non-negative integer",
                )
            counts[prefix] = count

    absent = _parse_paths(data.get("absent"), "absent", label, mount_paths)
    forbidden_data = data.get("forbidden")
    forbidden: tuple[str, ...] = ()
    if forbidden_data is not None:
        if (
            not isinstance(forbidden_data, list)
            or not forbidden_data
            or not all(isinstance(s, str) and s for s in forbidden_data)
        ):
            raise EvalCaseInvalidError(
                label,
                "expect_store: 'forbidden' must be a non-empty list of "
                "non-empty strings",
            )
        forbidden = tuple(forbidden_data)

    return StoreExpectation(
        documents=tuple(documents), counts=counts, absent=absent, forbidden=forbidden
    )


def _parse_paths(
    data: Any, key: str, label: str, mount_paths: frozenset[str]
) -> tuple[str, ...]:
    if data is None:
        return ()
    if not isinstance(data, list) or not all(isinstance(p, str) for p in data):
        raise EvalCaseInvalidError(
            label, f"expect_store: '{key}' must be a list of paths"
        )
    for path in data:
        _require_mounted(path, label, mount_paths, allow_root=False)
    return tuple(data)


def _parse_document(
    data: Any, label: str, mount_paths: frozenset[str]
) -> DocumentExpectation:
    if not isinstance(data, dict):
        raise EvalCaseInvalidError(label, "expect_store: each document is a mapping")
    for key in data:
        if key not in _DOCUMENT_KEYS:
            raise EvalCaseInvalidError(
                label, f"expect_store document: unknown key '{key}'"
            )
    path = data.get("path")
    path_prefix = data.get("path_prefix")
    if (path is None) == (path_prefix is None):
        raise EvalCaseInvalidError(
            label,
            "expect_store document needs exactly one of 'path' or 'path_prefix'",
        )
    where = path if path is not None else path_prefix
    if not isinstance(where, str):
        raise EvalCaseInvalidError(
            label, "expect_store document 'path'/'path_prefix' must be a string"
        )
    # An exact path names a document inside a mount; a prefix may be the
    # bare mount itself — naming is the model's, the region is ours.
    _require_mounted(where, label, mount_paths, allow_root=path is None)

    versions = data.get("versions")
    if versions is not None and (
        not isinstance(versions, int) or isinstance(versions, bool) or versions < 1
    ):
        raise EvalCaseInvalidError(
            label, f"document '{where}': 'versions' must be a positive integer"
        )

    actions: tuple[MemoryAction, ...] | None = None
    actions_data = data.get("actions")
    if actions_data is not None:
        if not isinstance(actions_data, list) or not actions_data:
            raise EvalCaseInvalidError(
                label, f"document '{where}': 'actions' must be a non-empty list"
            )
        for action in actions_data:
            if action not in _ACTIONS:
                raise EvalCaseInvalidError(
                    label,
                    f"document '{where}': unknown action {action!r} — one of "
                    "created, modified, deleted",
                )
        actions = tuple(actions_data)
        if versions is not None and len(actions) != versions:
            raise EvalCaseInvalidError(
                label,
                f"document '{where}': 'versions' ({versions}) and 'actions' "
                f"(length {len(actions)}) disagree",
            )

    return DocumentExpectation(
        path=path,
        path_prefix=path_prefix,
        content=parse_response(
            data.get("content"), label, f"document '{where}'", key_name="content"
        ),
        versions=versions,
        actions=actions,
    )


def _require_mounted(
    path: str, label: str, mount_paths: frozenset[str], *, allow_root: bool
) -> None:
    first, _, rest = path.strip("/").partition("/")
    if not first or first not in mount_paths:
        known = ", ".join(f"/{p}" for p in sorted(mount_paths))
        raise EvalCaseInvalidError(
            label,
            f"expect_store path '{path}' names no declared mount "
            f"(available: {known})",
        )
    if not rest and not allow_root:
        raise EvalCaseInvalidError(
            label,
            f"expect_store path '{path}' must name a document inside the mount",
        )
