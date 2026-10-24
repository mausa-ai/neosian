"""Source authority and snapshot continuity cannot be supplied by bundle labels."""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from tests.contract.exchange_support import SCOPE, export, save
from tests.contract.profile_support import PROFILE, bind, install, run
from tests.contract.support import ROOT, Binary, serve
from tests.contract.test_transport import TOKEN, responder


@pytest.mark.parametrize("failure", ["changed", "revoked", "unsupported"])
def test_source_must_remain_authorized_and_stable_through_export(
    binary: Binary, failure: str
) -> None:
    with serve(binary) as server:
        server.post("memory/write", scope=SCOPE, path="a", content="before")

        def reply(path: str, body: dict[str, Any]) -> tuple[int, Any]:
            if path == "/v1/capabilities":
                data = server.request(path, method="GET").body
                if failure == "unsupported":
                    data.pop("change_feed")
                return 200, data
            if path == "/v1/memory/read":
                if failure == "revoked":
                    return 403, {
                        "error": {
                            "code": "forbidden",
                            "message": "revoked",
                            "details": {},
                        }
                    }
                if failure == "changed":
                    server.post(
                        "memory/write", scope=SCOPE, path="a", content="during export"
                    )
            response = server.request(path, body)
            return response.status, response.body

        with responder(reply) as (url, _requests):
            output = binary.directory / "snapshot.json"
            result = run(
                binary,
                "exchange",
                "snapshot",
                "/memories",
                str(output),
                "--scope",
                SCOPE,
                "--url",
                url,
                "--paused",
                env={"NEOSIAN_CLIENT_TOKEN": TOKEN},
            )
            assert result.returncode == 1 and not output.exists()


def test_readonly_profile_exports_one_mount_without_capture_or_bindings(
    binary: Binary,
) -> None:
    profile = (
        PROFILE.replace('scope = "workspace:portable"', f'scope = "{SCOPE}"')
        .replace('skills = ["/project/skills/review"]', "skills = []")
        .replace('access = "rw"', 'access = "ro"')
    )
    install(binary, profile)
    bind(binary, "--root", str(binary.home))
    # Explicit legacy source writes take place before selecting the profile.
    binary.json(
        "memory",
        "create",
        "/memories/notes/a",
        "--content",
        "authorized",
        "--scope",
        SCOPE,
    )
    output = binary.directory / "profile-snapshot.json"
    result = run(
        binary,
        "exchange",
        "snapshot",
        "/project",
        str(output),
        "--profile",
        "portable",
        "--paused",
    )
    assert result.returncode == 0, result.stderr
    content = output.read_text()
    assert str(binary.home) not in content and "capture" not in content
    assert [u["path"] for u in json.loads(content)["updates"]] == ["notes/a"]
    denied = run(
        binary,
        "exchange",
        "snapshot",
        "/foreign",
        str(binary.directory / "denied.json"),
        "--profile",
        "portable",
        "--paused",
    )
    assert denied.returncode == 1


def test_literal_bundle_and_schema_preserve_opaque_cursor_and_json(
    binary: Binary,
) -> None:
    record = {
        "content": "A skill\r\n",
        "version": 17,
        "created_at": "2026-10-20T01:02:03Z",
        "updated_at": "2026-10-21T04:05:06Z",
        "actor": "opaque source actor",
        "redacted": False,
        "extra": {"nested": {"a": [1, None, True, "é"]}},
    }
    bundle = {
        "format": 1,
        "base": None,
        "checkpoint": {
            "format": 1,
            "scope": SCOPE,
            "stream": "11111111-2222-4333-8444-555555555555",
            "cursor": "opaque-\u0000-é",
        },
        "updates": [{"path": "skills/a", "document": record, "deletion": None}],
        "erasures": [],
    }
    path = save(binary.directory / "literal.json", bundle)
    schema = json.loads((ROOT / "assets/contracts/exchange-v1.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    validator.validate(bundle)
    validator.validate(bundle["checkpoint"])
    from tests.contract.exchange_support import apply, receiver

    apply(binary, path)
    snap = export(binary, "roundtrip", flags=receiver(binary))
    assert json.loads(snap.read_text())["updates"][0]["document"] == record
    status = binary.json("exchange", "status", *receiver(binary))
    assert status["status"] == "ready" and status["checkpoint"] == bundle["checkpoint"]


def test_existing_directory_cannot_poison_receiver_recovery(binary: Binary) -> None:
    from tests.contract.exchange_support import files, receiver

    binary.json(
        "memory", "create", "/memories/a", "--content", "body", "--scope", SCOPE
    )
    snap = export(binary, "snapshot")
    (binary.directory / "replica/user%3Aexchange/documents/a.md").mkdir(parents=True)
    result = binary.run("exchange", "apply", str(snap), *receiver(binary), "--write")
    assert result.returncode == 1 and "snapshot_required" in result.stdout
    assert not files(binary)
    assert binary.json("exchange", "status", *receiver(binary))["status"] == "empty"


def test_nonportable_paths_refuse_before_touching_the_receiver(
    binary: Binary,
) -> None:
    from tests.contract.exchange_support import files, receiver

    binary.json(
        "memory", "create", "/memories/a", "--content", "body", "--scope", SCOPE
    )
    snap = export(binary, "snapshot")
    bundle = json.loads(snap.read_text())
    first = bundle["updates"][0]
    bundle["updates"] = [{**first, "path": "café"}, {**first, "path": "cafe\u0301"}]
    bad = save(binary.directory / "aliases.json", bundle)
    result = binary.run("exchange", "apply", str(bad), *receiver(binary), "--write")
    assert result.returncode == 1 and "memory_path_invalid" in result.stdout
    assert not files(binary)
