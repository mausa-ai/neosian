"""Current knowledge ingestion and round trips through the public interfaces."""

from __future__ import annotations

import hashlib
import json

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from tests.contract.knowledge_support import bundle, invoke, marked, success, unpack
from tests.contract.profile_support import PROFILE, bind, install, rpc
from tests.contract.support import ROOT, Binary, serve


@pytest.mark.parametrize("mode", ["file", "http", "profile"])
def test_markdown_preview_import_search_export_and_repeat(
    binary: Binary, mode: str
) -> None:
    source = binary.directory / "notes"
    source.mkdir()
    body = "---\r\ntitle: A foreign note\r\n---\r\n# Café\r\nKeep the reasoning.\r\n"
    (source / "decision.md").write_bytes(body.encode())
    (source / "image.png").write_bytes(b"ignored attachment")
    with serve(binary) as server:
        env = {"NEOSIAN_CLIENT_TOKEN": server.token}
        flags = ["--scope", "user:knowledge"]
        destination = "/memories/notes"
        scope = "user:knowledge"
        if mode == "http":
            flags += ["--url", server.url]
        elif mode == "profile":
            install(binary, PROFILE)
            bind(binary, "--root", str(binary.home))
            flags = ["--profile", "portable"]
            destination = "/project/notes"
            scope = "workspace:portable"
        args = ["import", str(source), "--to", destination]
        preview = success(invoke(binary, *args, flags=flags, env=env))
        assert preview["preview"] and preview["applied"] == []
        assert preview["ignored"] == ["image.png"]
        assert preview["actions"] == [
            {"path": destination + "/decision", "action": "create"}
        ]
        assert (
            server.post("memory/read", scope=scope, path="notes/decision")["document"]
            is None
        )
        applied = success(invoke(binary, *args, "--write", flags=flags, env=env))
        assert applied["applied"] == [{"path": destination + "/decision", "version": 1}]
        document = server.post("memory/read", scope=scope, path="notes/decision")[
            "document"
        ]
        metadata, actual_body = unpack(document["content"])
        assert actual_body == body
        assert metadata["collection"] == "notes"
        assert metadata["sources"] == [
            {
                "label": "notes",
                "file": "decision.md",
                "sha256": hashlib.sha256(body.encode()).hexdigest(),
            }
        ]
        repeat = success(invoke(binary, *args, "--write", flags=flags, env=env))
        assert repeat["actions"][0]["action"] == "duplicate" and repeat["applied"] == []
        hits = server.post("memory/search", scope=scope, query="reasoning")["hits"]
        assert [hit["path"] for hit in hits] == ["notes/decision"]
        exported = binary.directory / "export"
        success(
            invoke(
                binary,
                "export",
                destination,
                "--output",
                str(exported),
                flags=flags,
                env=env,
            )
        )
        assert (exported / "documents/decision.md").read_bytes() == document[
            "content"
        ].encode()
        schema = json.loads(
            (ROOT / "assets/contracts/knowledge-v1.schema.json").read_text()
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(
            json.loads((exported / "knowledge.json").read_text())
        )
        imported = success(
            invoke(
                binary,
                "import",
                str(exported),
                "--format",
                "bundle",
                "--to",
                destination + "-copy",
                "--write",
                flags=flags,
                env=env,
            )
        )
        assert imported["applied"][0]["version"] == 1
        repeated = success(
            invoke(
                binary,
                "import",
                str(exported),
                "--format",
                "bundle",
                "--to",
                destination + "-copy",
                "--write",
                flags=flags,
                env=env,
            )
        )
        assert (
            repeated["actions"][0]["action"] == "duplicate"
            and repeated["applied"] == []
        )
        copied = server.post("memory/read", scope=scope, path="notes-copy/decision")[
            "document"
        ]
        provenance, copied_body = unpack(copied["content"])
        assert copied_body == body
        assert provenance["collection"] == "notes-copy"
        assert provenance["sources"][0] == metadata["sources"][0]
        assert provenance["sources"][-1]["origin"]["version"] == 1
        assert provenance["sources"][-1]["origin"]["scope"] == scope


def test_conflicts_preflight_entire_batch_skip_and_replace(binary: Binary) -> None:
    source = binary.directory / "input"
    source.mkdir()
    (source / "a.md").write_text("First", encoding="utf-8")
    args = ["import", str(source), "--to", "/memories/notes"]
    success(invoke(binary, *args, "--write"))
    (source / "a.md").write_text("Changed", encoding="utf-8")
    (source / "b.md").write_text("New", encoding="utf-8")
    with serve(binary) as server:
        failed = invoke(binary, *args, "--write")
        assert failed.returncode == 1
        assert json.loads(failed.stdout)["applied"] == []
        assert (
            server.post("memory/read", scope="user:knowledge", path="notes/b")[
                "document"
            ]
            is None
        )
        skipped = success(invoke(binary, *args, "--conflict", "skip", "--write"))
        assert [a["action"] for a in skipped["actions"]] == ["skip", "create"]
        changed = success(invoke(binary, *args, "--conflict", "replace", "--write"))
        assert changed["applied"] == [{"path": "/memories/notes/a", "version": 2}]
        versions = server.post(
            "memory/versions", scope="user:knowledge", path="notes/a", limit=10
        )["versions"]
        assert [unpack(v["content"])[1] for v in versions] == ["Changed", "First"]


@pytest.mark.parametrize("action", ["delete", "redact"])
def test_import_cannot_resurrect_erased_or_deleted_memory(
    binary: Binary, action: str
) -> None:
    file = binary.directory / "a.md"
    file.write_text("erasure-sensitive", encoding="utf-8")
    args = ["import", str(file), "--to", "/memories/notes", "--write"]
    success(invoke(binary, *args))
    with serve(binary) as server:
        server.post("memory/" + action, scope="user:knowledge", path="notes/a")
        denied = invoke(binary, *args, "--conflict", "replace")
        assert denied.returncode == 1
        assert "refusing" in denied.stderr
        assert (
            server.post(
                "memory/search", scope="user:knowledge", query="erasure-sensitive"
            )["hits"]
            == []
        )
        exported = binary.directory / "export"
        result = success(
            invoke(binary, "export", "/memories/notes", "--output", str(exported))
        )
        assert result["documents"] == 0


def test_collection_rules_are_optional_checked_and_exported(binary: Binary) -> None:
    source = bundle(
        binary.directory / "bundle",
        marked("A decision", attributes={"kind": "decision", "count": 2}),
    )
    definition = {
        "format": 1,
        "name": "My decisions",
        "schema": {
            "allow_unknown": False,
            "fields": {
                "kind": {
                    "type": "string",
                    "required": True,
                    "values": ["decision", "fact"],
                },
                "count": {"type": "integer"},
            },
        },
    }
    rules = binary.directory / "rules.json"
    rules.write_text(json.dumps(definition), encoding="utf-8")
    preview = success(
        invoke(binary, "collection", "/memories/notes", "--file", str(rules))
    )
    assert preview["preview"] and preview["applied"] == []
    success(
        invoke(binary, "collection", "/memories/notes", "--file", str(rules), "--write")
    )
    success(
        invoke(
            binary,
            "import",
            str(source),
            "--format",
            "bundle",
            "--to",
            "/memories/notes",
            "--write",
        )
    )
    checked = success(invoke(binary, "validate", "/memories/notes"))
    assert (
        checked["checked"] == 1
        and checked["schema_enabled"]
        and checked["issues"] == []
    )
    exported = binary.directory / "export"
    success(invoke(binary, "export", "/memories/notes", "--output", str(exported)))
    assert (
        json.loads((exported / "knowledge.json").read_text())["collection"]["name"]
        == "My decisions"
    )
    success(
        invoke(
            binary,
            "import",
            str(exported),
            "--format",
            "bundle",
            "--to",
            "/memories/copied",
            "--write",
        )
    )
    assert success(invoke(binary, "validate", "/memories/copied"))["schema_enabled"]
    # An ordinary edit can violate advisory rules; explicit checks explain the failure.
    with serve(binary) as server:
        server.post(
            "memory/write",
            scope="user:knowledge",
            path="notes/a",
            content=marked("Changed", attributes={"count": 2.5, "unknown": True}),
        )
    invalid = invoke(binary, "validate", "/memories/notes")
    assert invalid.returncode == 1
    issues = {i["field"]: i["message"] for i in json.loads(invalid.stdout)["issues"]}
    assert issues == {
        "kind": "required field is missing",
        "count": "expected integer",
        "unknown": "unknown field is not allowed",
    }
    plain = binary.directory / "b.md"
    plain.write_text("Unstructured text", encoding="utf-8")
    rejected = invoke(
        binary, "import", str(plain), "--to", "/memories/notes", "--write"
    )
    assert rejected.returncode == 1 and json.loads(rejected.stdout)["applied"] == []
    success(
        invoke(
            binary, "import", str(plain), "--to", "/memories/unstructured", "--write"
        )
    )


def test_bundle_source_metadata_and_existing_envelopes_survive(binary: Binary) -> None:
    source = bundle(
        binary.directory / "bundle",
        marked("Imported", attributes={"nested": {"keep": [None, True, 4]}}),
    )
    success(
        invoke(
            binary,
            "import",
            str(source),
            "--format",
            "bundle",
            "--to",
            "/memories/notes",
            "--write",
        )
    )
    with serve(binary) as server:
        document = server.post("memory/read", scope="user:knowledge", path="notes/a")[
            "document"
        ]
        data, _ = unpack(document["content"])
        origin = data["sources"][0]["origin"]
        assert origin["version"] == 17 and origin["actor"] == "opaque original actor"
        assert origin["extra"] == {"foreign": {"nested": [1, None, "preserved"]}}
        assert document["version"] == 1
        assert data["attributes"] == {"nested": {"keep": [None, True, 4]}}
    install(binary, PROFILE.replace("workspace:portable", "user:knowledge"))
    bind(binary, "--root", str(binary.home))
    answers = rpc(binary, [("memory", {"command": "view", "path": "/project/notes/a"})])
    assert "Imported" in json.dumps(answers[2])


@pytest.mark.parametrize("ending", ["\n", "\r\n"], ids=["LF", "CRLF"])
def test_marked_headers_accept_platform_line_endings_without_normalizing_body(
    binary: Binary, ending: str
) -> None:
    text = marked("First\nSecond\n").replace("\n", ending)
    file = binary.directory / "a.md"
    file.write_bytes(text.encode())
    with serve(binary) as server:
        definition = (
            '<!-- neosian-collection:1\n{"format":1,"name":"My notes"}\n-->\n'.replace(
                "\n", ending
            )
        )
        server.post(
            "memory/write",
            scope="user:knowledge",
            path="notes/.collection",
            content=definition,
        )
        assert (
            success(invoke(binary, "collection", "/memories/notes"))["definition"][
                "name"
            ]
            == "My notes"
        )
        success(
            invoke(binary, "import", str(file), "--to", "/memories/notes", "--write")
        )
        document = server.post("memory/read", scope="user:knowledge", path="notes/a")[
            "document"
        ]
        assert unpack(document["content"])[1] == f"First{ending}Second{ending}"
