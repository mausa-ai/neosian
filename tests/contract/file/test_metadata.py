"""Wire operations preserve supported metadata and refuse unsupported YAML."""

from tests.contract.file.support import DOCUMENT, SCOPE, snapshot
from tests.contract.storage.support import refused
from tests.contract.support import Binary, serve


def add_metadata(binary: Binary, text: str) -> None:
    path = binary.home / DOCUMENT
    before = path.read_bytes()
    head, body = before[4:].split(b"---\n", 1)
    changed = b"---\n" + head + text.encode() + b"---\n" + body
    path.write_bytes(changed)


def test_block_metadata_refuses_wire_mutations_without_changing_data(
    planted: Binary,
) -> None:
    add_metadata(planted, "nested:\n  flags: [true, null, 7]\n  title: 'café'\n")
    before = snapshot(planted.home)
    with serve(planted) as server:
        for route, fields in [
            ("read", {}),
            ("write", {"content": "must not be written"}),
            ("rename", {"dst": "moved"}),
        ]:
            body = {"scope": SCOPE, "path": "notes/fact", **fields}
            if route == "rename":
                body["src"] = body.pop("path")
            refused(
                server.request("/v1/memory/" + route, body),
                "memory_format_unsupported",
            )
            assert snapshot(planted.home) == before


def test_nested_json_metadata_survives_wire_reads_and_writes(planted: Binary) -> None:
    add_metadata(planted, '"nested": {"flags":[true,null,7],"title":"café"}\n')
    with serve(planted) as server:
        document = server.post("memory/read", scope=SCOPE, path="notes/fact")[
            "document"
        ]
        assert document["extra"]["nested"] == {
            "flags": [True, None, 7],
            "title": "café",
        }
        rewritten = server.post(
            "memory/write", scope=SCOPE, path="notes/fact", content="updated"
        )["document"]
        assert rewritten["extra"] == document["extra"]
