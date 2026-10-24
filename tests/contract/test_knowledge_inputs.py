"""Refusals preserve source bytes and never create destination documents."""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from tests.contract.knowledge_support import bundle, invoke, marked, success
from tests.contract.support import Binary, serve


@pytest.mark.parametrize(
    "path",
    [
        "/memories",
        "notes",
        "//memories/notes",
        "/memories/notes/",
        "/memories/../notes",
        "/memories/sessions",
        "/memories/messages/a",
        "/memories/message-links/a",
        "/memories/handoff",
        "/memories/HANDOFF",
        "/memories/SESSIONS/entry",
        "/memories/notes/.Collection",
        "/memories/notes/.collection",
    ],
)
def test_invalid_destination_has_no_store_side_effect(
    binary: Binary, path: str
) -> None:
    file = binary.directory / "a.md"
    file.write_text("hello", encoding="utf-8")
    refused = invoke(binary, "import", str(file), "--to", path, "--write")
    assert refused.returncode == 2 and not binary.home.exists()
    assert file.read_text() == "hello"


@pytest.mark.parametrize(
    "text",
    [
        "<!-- neosian-knowledge:2\n{}\n-->\nbody",
        "<!-- neosian-knowledge:1\n{}",
        marked("body", format=99),
        marked("body", unknown=True),
        marked("body", references=[{"relation": "related", "path": "../escape"}]),
        marked("body", sources=[{"label": "forged", "file": "a", "sha256": "wrong"}]),
    ],
)
def test_unsupported_or_malformed_metadata_refuses_without_mutation(
    binary: Binary, text: str
) -> None:
    file = binary.directory / "a.md"
    file.write_bytes(text.encode())
    refused = invoke(binary, "import", str(file), "--to", "/memories/notes", "--write")
    assert refused.returncode == 2 and not binary.home.exists()
    assert file.read_bytes() == text.encode()


@pytest.mark.parametrize(
    "change",
    [
        {"format": 99},
        {"unexpected": True},
        {"file": "../escape.md"},
        {"file": "/etc/passwd"},
        {"sha256": "0" * 64},
        {"path": "../escape"},
        {"path": ".collection"},
        {"path": "sessions/secret"},
        {"origin": {"scope": "user:x"}},
    ],
)
def test_bundle_manifest_is_strict_and_contained(
    binary: Binary, change: dict[str, Any]
) -> None:
    source = bundle(binary.directory / "bundle", "body")
    manifest = source / "knowledge.json"
    data = json.loads(manifest.read_text())
    if set(change) & {"format", "unexpected"}:
        data.update(change)
    else:
        data["documents"][0].update(change)
    manifest.write_text(json.dumps(data), encoding="utf-8")
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    rejected = invoke(
        binary,
        "import",
        str(source),
        "--format",
        "bundle",
        "--to",
        "/memories/notes",
        "--write",
    )
    assert rejected.returncode == 2 and not binary.home.exists()
    assert before == {p.name: p.read_bytes() for p in source.iterdir()}


@pytest.mark.skipif(os.name == "nt", reason="symlink fixture requires Unix")
@pytest.mark.parametrize("nested", [False, True])
def test_symlinks_do_not_import_outside_files(binary: Binary, nested: bool) -> None:
    source = binary.directory / "input"
    source.mkdir()
    secret = binary.directory / "secret.md"
    secret.write_text("not selected", encoding="utf-8")
    (source / "a.md").symlink_to(secret)
    chosen = source if nested else source / "a.md"
    refused = invoke(
        binary, "import", str(chosen), "--to", "/memories/notes", "--write"
    )
    assert refused.returncode == 2 and not binary.home.exists()
    assert "symlink" in refused.stderr


def test_duplicate_paths_and_case_collisions_are_portably_refused(
    binary: Binary,
) -> None:
    source = bundle(binary.directory / "bundle", "body")
    manifest = source / "knowledge.json"
    data = json.loads(manifest.read_text())
    data["documents"].append({**data["documents"][0], "path": "A"})
    manifest.write_text(json.dumps(data), encoding="utf-8")
    rejected = invoke(
        binary,
        "import",
        str(source),
        "--format",
        "bundle",
        "--to",
        "/memories/notes",
        "--write",
    )
    assert rejected.returncode == 2 and not binary.home.exists()
    file = binary.directory / "new.md"
    file.write_text("new", encoding="utf-8")
    with serve(binary) as server:
        server.post(
            "memory/write", scope="user:knowledge", path="Notes/old", content="keep"
        )
        refused = invoke(
            binary, "import", str(file), "--to", "/memories/notes", "--write"
        )
        assert refused.returncode == 1 and "case-colliding" in refused.stderr
        assert (
            server.post("memory/read", scope="user:knowledge", path="Notes/old")[
                "document"
            ]["content"]
            == "keep"
        )


@pytest.mark.parametrize(
    "contents",
    [b"\xff", b"x" * (1024 * 1024 + 1), b"text\x00binary"],
    ids=["invalid-utf8", "oversized", "nul-byte"],
)
def test_invalid_encoding_and_oversized_body_are_refused(
    binary: Binary, contents: bytes
) -> None:
    file = binary.directory / "a.md"
    file.write_bytes(contents)
    refused = invoke(binary, "import", str(file), "--to", "/memories/notes", "--write")
    assert refused.returncode == 2 and not binary.home.exists()


@pytest.mark.parametrize(
    "definition",
    [
        {"format": 2, "name": "Future"},
        {
            "format": 1,
            "name": "Rules",
            "schema": {"fields": {"a": {"type": "mystery"}}},
        },
        {
            "format": 1,
            "name": "Rules",
            "schema": {"fields": {"a": {"type": "integer", "values": ["string"]}}},
        },
        {"format": 1, "name": "Rules", "policy": "grant-all"},
    ],
)
def test_collection_definition_is_versioned_and_strict(
    binary: Binary, definition: dict[str, Any]
) -> None:
    file = binary.directory / "collection.json"
    file.write_text(json.dumps(definition), encoding="utf-8")
    refused = invoke(
        binary, "collection", "/memories/notes", "--file", str(file), "--write"
    )
    assert refused.returncode == 2 and not binary.home.exists()


def test_exports_refuse_existing_directory_and_nested_collections(
    binary: Binary,
) -> None:
    output = binary.directory / "out"
    output.mkdir()
    (output / "keep").write_text("preserve", encoding="utf-8")
    refused = invoke(binary, "export", "/memories/notes", "--output", str(output))
    assert refused.returncode == 1 and (output / "keep").read_text() == "preserve"
    definition = binary.directory / "rules.json"
    definition.write_text('{"format":1,"name":"Nested"}', encoding="utf-8")
    success(
        invoke(
            binary,
            "collection",
            "/memories/notes/nested",
            "--file",
            str(definition),
            "--write",
        )
    )
    refused = invoke(
        binary,
        "export",
        "/memories/notes",
        "--output",
        str(binary.directory / "new-export"),
    )
    assert refused.returncode == 1 and "nested collections" in refused.stderr
    assert not (binary.directory / "new-export").exists()


@pytest.mark.parametrize("kind", ["document", "collection", "bundle"])
def test_duplicate_json_keys_are_not_silently_discarded(
    binary: Binary, kind: str
) -> None:
    if kind == "collection":
        file = binary.directory / "rules.json"
        file.write_text('{"format":1,"name":"First","name":"Second"}', encoding="utf-8")
        args = ["collection", "/memories/notes", "--file", str(file), "--write"]
    elif kind == "document":
        file = binary.directory / "a.md"
        file.write_text(
            marked("body").replace('"attributes": {}', '"attributes": {"a":1,"a":2}'),
            encoding="utf-8",
        )
        args = ["import", str(file), "--to", "/memories/notes", "--write"]
    else:
        source = bundle(binary.directory / "bundle", "body")
        file = source / "knowledge.json"
        file.write_text(
            file.read_text().replace('"format": 1', '"format": 1, "format": 1'),
            encoding="utf-8",
        )
        args = [
            "import",
            str(source),
            "--format",
            "bundle",
            "--to",
            "/memories/notes",
            "--write",
        ]
    refused = invoke(binary, *args)
    assert refused.returncode == 2 and "duplicate JSON key" in refused.stderr
    assert not binary.home.exists()


def test_new_bundle_rules_cannot_invalidate_retained_documents(binary: Binary) -> None:
    file = binary.directory / "old.md"
    file.write_text("Existing unstructured document", encoding="utf-8")
    success(invoke(binary, "import", str(file), "--to", "/memories/notes", "--write"))
    source = bundle(
        binary.directory / "bundle",
        marked("New", attributes={"owner": "Ada"}),
        collection={
            "format": 1,
            "name": "Rules",
            "schema": {"fields": {"owner": {"type": "string", "required": True}}},
        },
    )
    rejected = invoke(
        binary,
        "import",
        str(source),
        "--format",
        "bundle",
        "--to",
        "/memories/notes",
        "--write",
        "--conflict",
        "replace",
    )
    assert rejected.returncode == 1
    report = json.loads(rejected.stdout)
    assert report["applied"] == []
    assert report["issues"] == [
        {"path": "notes/old", "field": "owner", "message": "required field is missing"}
    ]
    with serve(binary) as server:
        assert (
            server.post(
                "memory/read", scope="user:knowledge", path="notes/.collection"
            )["document"]
            is None
        )


@pytest.mark.skipif(os.name == "nt", reason="backslash is a separator on Windows")
def test_literal_backslash_filenames_cannot_change_the_selected_source(
    binary: Binary,
) -> None:
    source = binary.directory / "source"
    source.mkdir()
    (source / r"a\b.md").write_text("Literal filename", encoding="utf-8")
    (source / "a").mkdir()
    (source / "a/b.md").write_text("Different file", encoding="utf-8")
    result = invoke(binary, "import", str(source), "--to", "/memories/notes", "--write")
    assert result.returncode == 2 and not binary.home.exists()
    assert "backslashes" in result.stderr
