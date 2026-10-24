"""All four memory cursors, followed with bounded requests and no SDK paging code."""

from tests.contract.storage.support import Storage, refused


def test_memory_pages_are_complete_and_nonoverlapping(storage: Storage) -> None:
    storage.require("pageable")
    for path in ["a", "b", "c"]:
        for content in ["one", "two", "three"]:
            storage.memory("write", path=path, content=content)
        storage.memory("redact", path=path)
    for route, key, arguments in [
        ("list_documents", "entries", {}),
        ("versions", "versions", {"path": "a"}),
        ("history", "versions", {}),
        ("redactions", "redactions", {}),
    ]:
        whole = storage.memory(route, **arguments)[key]
        cursor = None
        seen = []
        cursors = set()
        for _ in range(10):
            page = storage.memory(route, **arguments, cursor=cursor, limit=2)
            assert len(page[key]) <= 2
            seen.extend(page[key])
            cursor = page["next_cursor"]
            if cursor is None:
                break
            assert cursor not in cursors, "cursor failed to advance"
            cursors.add(cursor)
        else:
            raise AssertionError("paging did not terminate")
        assert seen == whole
    # Tokens are opaque: a custom store need not encode them like FileStore.
    # Their method binding is public, so reuse a real token on the wrong route.
    cursor = storage.memory("list_documents", limit=1)["next_cursor"]
    assert cursor is not None
    refused(
        storage.server.request(
            "/v1/memory/versions",
            {"scope": storage.scope, "path": "a", "cursor": cursor},
        ),
        "value_error",
    )
