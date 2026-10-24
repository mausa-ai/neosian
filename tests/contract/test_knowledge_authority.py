"""Knowledge references are data, not grants or implicit cross-scope exports."""

from __future__ import annotations

import json

import pytest

from tests.contract.knowledge_support import invoke, marked, success
from tests.contract.profile_support import PROFILE, bind, grant_env, install, rpc
from tests.contract.support import Binary, serve
from tests.contract.test_transport import TOKEN, responder


def test_references_follow_current_authority_and_report_path_changes(
    binary: Binary,
) -> None:
    with serve(binary) as server:
        source = marked(
            "A",
            references=[
                {"relation": "related", "path": "b"},
                {"relation": "external", "scope": "user:private", "path": "secret"},
            ],
        )
        for scope, path, content in [
            ("user:knowledge", "notes/a", source),
            ("user:knowledge", "notes/b", "target"),
            ("user:private", "secret", "hidden"),
        ]:
            server.post("memory/write", scope=scope, path=path, content=content)
        results = success(invoke(binary, "references", "/memories/notes/a"))[
            "references"
        ]
        assert [r["status"] for r in results] == ["available", "unavailable"]
        assert results[0]["reference"] == "/memories/notes/b"
        assert "hidden" not in json.dumps(results)
        server.post(
            "memory/rename", scope="user:knowledge", src="notes/b", dst="notes/c"
        )
        assert (
            success(invoke(binary, "references", "/memories/notes/a"))["references"][0][
                "status"
            ]
            == "missing"
        )
        server.post(
            "memory/write", scope="user:knowledge", path="notes/b", content="again"
        )
        server.post("memory/redact", scope="user:knowledge", path="notes/b")
        assert (
            success(invoke(binary, "references", "/memories/notes/a"))["references"][0][
                "status"
            ]
            == "unavailable"
        )
        assert (
            server.post("memory/read", scope="user:knowledge", path="notes/a")[
                "document"
            ]["content"]
            == source
        )
        output = binary.directory / "export"
        success(invoke(binary, "export", "/memories/notes", "--output", str(output)))
        assert "hidden" not in (output / "knowledge.json").read_text()
        assert not (output / "documents/secret.md").exists()


@pytest.mark.parametrize("access", ["ro", "eo"])
def test_mount_permissions_cover_imports_and_collection_changes(
    binary: Binary, access: str
) -> None:
    file = binary.directory / "a.md"
    file.write_text("source", encoding="utf-8")
    flags = ["--mount", f"scope=user:knowledge,path=notes,{access}"]
    refused = invoke(
        binary, "import", str(file), "--to", "/notes/collection", "--write", flags=flags
    )
    assert refused.returncode == 1 and (
        "read-only" in refused.stderr or "edit-only" in refused.stderr
    )


def test_server_grants_deny_import_and_reference_scope_forgery(binary: Binary) -> None:
    env = grant_env(binary.directory / "grants.toml")
    with serve(binary, extra_env=env) as server:
        server.post(
            "memory/write",
            scope="workspace:portable",
            path="notes/a",
            content=marked(
                "permitted",
                references=[
                    {"relation": "forged", "scope": "workspace:other", "path": "secret"}
                ],
            ),
        )
        server.post(
            "memory/write",
            scope="workspace:other",
            path="secret",
            content="hidden target",
        )
        profile = (
            PROFILE
            + '\n[[mounts]]\nsource = "knowledge"\nscope = "workspace:other"\npath = "other"\naccess = "ro"\ninjection = "none"\n'
        )
        install(binary, profile)
        bind(binary, "--url", server.url, "--token-env", "KNOWLEDGE_TOKEN")
        flags = ["--profile", "portable"]
        token = {"KNOWLEDGE_TOKEN": "reader-token"}
        references = success(
            invoke(binary, "references", "/project/notes/a", flags=flags, env=token)
        )["references"]
        assert (
            references[0]["status"] == "unavailable"
            and references[0]["version"] is None
        )
        file = binary.directory / "b.md"
        file.write_text("incoming", encoding="utf-8")
        denied = invoke(
            binary,
            "import",
            str(file),
            "--to",
            "/project/notes",
            "--write",
            flags=flags,
            env=token,
        )
        assert denied.returncode == 1
        assert "hidden target" not in denied.stdout + denied.stderr
        assert (
            server.post("memory/read", scope="workspace:portable", path="notes/b")[
                "document"
            ]
            is None
        )
        responses = rpc(
            binary,
            [("memory", {"command": "view", "path": "/project/notes/a"})],
            env=token,
        )
        assert "permitted" in json.dumps(responses[-1])


def test_partial_remote_failure_reports_landed_documents(binary: Binary) -> None:
    source = binary.directory / "input"
    source.mkdir()
    for name in ["a", "b"]:
        (source / f"{name}.md").write_text(name, encoding="utf-8")
    written: list[str] = []

    def reply(path: str, body: dict[str, object]) -> tuple[int, object]:
        if path == "/v1/capabilities":
            return 200, {"wire_version": 6, "supports_optimistic_concurrency": True}
        if path == "/v1/memory/read":
            return 200, {"document": None}
        if path == "/v1/memory/versions":
            return 200, {"versions": [], "next_cursor": None}
        if path == "/v1/memory/list_documents":
            return 200, {"entries": [], "next_cursor": None}
        assert path == "/v1/memory/write"
        if body["path"] == "notes/b":
            return 500, {
                "error": {
                    "code": "agent_configuration_error",
                    "message": "injected outage",
                    "details": {},
                }
            }
        written.append(str(body["path"]))
        return 200, {
            "document": {
                "scope": "user:knowledge",
                "path": "notes/a",
                "content": body["content"],
                "version": 1,
                "created_at": "2026-10-24T10:00:00Z",
                "updated_at": "2026-10-24T10:00:00Z",
                "actor": "client:test",
                "redacted": False,
                "extra": {},
            }
        }

    with responder(reply) as (url, _):
        result = invoke(
            binary,
            "import",
            str(source),
            "--to",
            "/memories/notes",
            "--write",
            flags=["--scope", "user:knowledge", "--url", url],
            env={"NEOSIAN_CLIENT_TOKEN": TOKEN},
        )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert written == ["notes/a"], report
    assert report["applied"] == [{"path": "/memories/notes/a", "version": 1}]
    assert not report["success"] and "outage" in report["error"]
