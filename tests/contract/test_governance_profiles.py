"""Profile policy precedence, agent context and separate access authority."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.contract.governance_support import KEY, governed, proposal, review_hash
from tests.contract.knowledge_support import invoke, marked, success, unpack
from tests.contract.profile_support import PROFILE, bind, grant_env, install, rpc, run
from tests.contract.support import Binary, serve
from tests.contract.test_transport import TOKEN, responder
from tests.contract.upkeep_support import invoke as upkeep, save


@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.parametrize("injection", ["index", "full"])
def test_profile_policy_filters_context_search_reads_and_skills(
    binary: Binary, remote: bool, injection: str
) -> None:
    env = grant_env(binary.directory / "grants.toml")
    scope = "workspace:portable"
    with serve(binary, extra_env=env) as server:
        for path, body, fields in [
            ("notes/current", "needle CURRENT_CONTENT", {}),
            ("notes/draft", "needle DRAFT_CONTENT", {"state": "draft"}),
            (
                "notes/expired",
                "needle EXPIRED_CONTENT",
                {"expires_at": "2000-01-02T00:00:00Z"},
            ),
            (
                "skills/review",
                "---\nname: review\ndescription: UNREVIEWED_SKILL\n---\nDo the thing",
                {"state": "draft"},
            ),
        ]:
            server.post(
                "memory/write",
                scope=scope,
                path=path,
                content=governed(scope, path, body, **fields),
            )
        text = PROFILE.replace(
            'injection = "index"',
            f'injection = "{injection}"\nknowledge_policy = "approved"',
        )
        if injection == "full":
            text += 'documents = ["notes/current", "notes/draft", "notes/expired"]\n'
        install(binary, text)
        credentials: dict[str, str] = {}
        if remote:
            bind(binary, "--url", server.url, "--token-env", "KNOWLEDGE_TEST_TOKEN")
            credentials = {"KNOWLEDGE_TEST_TOKEN": "reader-token"}
        else:
            bind(binary, "--root", str(binary.home))
        answers = rpc(
            binary,
            [
                ("search_memory", {"query": "needle", "knowledge_policy": "all"}),
                (
                    "knowledge",
                    {
                        "command": "read",
                        "path": "/project/notes/draft",
                        "policy": "all",
                    },
                ),
                ("knowledge", {"command": "read", "path": "/project/notes/current"}),
                ("load_skill", {"name": "/project/skills/review"}),
                ("memory", {"command": "view", "path": "/project/notes/draft"}),
            ],
            env=credentials,
        )
        context = answers[0]["result"]["instructions"]
        assert (
            "DRAFT_CONTENT" not in context
            and "EXPIRED_CONTENT" not in context
            and "UNREVIEWED_SKILL" not in context
        )
        if injection == "index":
            assert (
                "/project/notes/draft" not in context
                and "/project/notes/expired" not in context
            )
        else:
            assert "CURRENT_CONTENT" in context

        def data(index: int) -> Any:
            return json.loads(answers[index]["result"]["content"][0]["text"])

        assert [h["path"] for h in data(2)["hits"]] == ["notes/current"]
        assert data(3)["policy"] == "approved" and "content" not in data(3)
        assert data(4)["included"] and data(4)["source"] == "knowledge"
        assert answers[5]["result"]["isError"] is True
        assert "DRAFT_CONTENT" in answers[6]["result"]["content"][0]["text"]
        result = success(
            run(
                binary,
                "memory",
                "search",
                "needle",
                "--profile",
                "portable",
                "--knowledge-policy",
                "all",
                "--json",
                env=credentials,
            )
        )
        assert [h["path"] for h in result["hits"]] == ["notes/current"]
        exported = binary.run("profile", "export", "portable")
        assert 'knowledge_policy = "approved"' in exported.stdout
        assert server.url not in exported.stdout


@pytest.mark.parametrize("target", ["skills/dependency", "skills/review/context.txt"])
def test_skill_dependencies_and_resources_obey_their_mount_policy(
    binary: Binary, target: str
) -> None:
    scope = "workspace:portable"
    with serve(binary) as server:
        body = (
            "---\nname: review\ndescription: Reviewed skill\n---\nUse current policy."
        )
        metadata, _ = unpack(governed(scope, "skills/review", body))
        if target == "skills/dependency":
            metadata["attributes"]["skill"] = {
                "format": 1,
                "requires": [{"skill": "dependency"}],
            }
            target_body = (
                "---\nname: dependency\ndescription: dependency\n---\nDependency body"
            )
        else:
            import hashlib

            target_body = "resource body"
            metadata["attributes"]["skill"] = {
                "format": 1,
                "files": {
                    "context.txt": hashlib.sha256(target_body.encode()).hexdigest()
                },
            }
        raw = marked(body, **metadata)
        metadata["attributes"][KEY]["review"]["sha256"] = review_hash(
            scope, "skills/review", raw
        )
        server.post(
            "memory/write",
            scope=scope,
            path="skills/review",
            content=marked(body, **metadata),
        )
        server.post(
            "memory/write",
            scope=scope,
            path=target,
            content=governed(scope, target, target_body, state="draft"),
        )
        install(
            binary,
            PROFILE.replace(
                'skills = ["/project/skills/review"]',
                'skills = ["/project/skills/review", "/project/skills/dependency"]',
            )
            + 'knowledge_policy = "approved"\n',
        )
        bind(binary, "--root", str(binary.home))
        result = run(
            binary,
            "skill",
            "load",
            "/project/skills/review",
            "--profile",
            "portable",
            "--json",
        )
        assert result.returncode != 0
        assert "excluded by knowledge policy" in result.stdout + result.stderr
        server.post(
            "memory/write",
            scope=scope,
            path=target,
            content=governed(scope, target, target_body),
        )
        result = run(
            binary,
            "skill",
            "load",
            "/project/skills/review",
            "--profile",
            "portable",
            "--json",
        )
        assert result.returncode == 0, result.stdout + result.stderr


def test_permission_revocation_covers_raw_cli_and_mcp_read_write_paths(
    binary: Binary,
) -> None:
    grants = binary.directory / "grants.toml"
    env = grant_env(grants)
    scope = "workspace:portable"
    with serve(binary, extra_env=env) as server:
        for path in ("notes/a", "notes/z"):
            server.post(
                "memory/write", scope=scope, path=path, content=governed(scope, path)
            )
        server.post(
            "memory/write",
            scope="workspace:other",
            path="notes/secret",
            content="PRIVATE_ORG_DATA",
        )
        install(
            binary,
            PROFILE.replace('injection = "index"', 'injection = "none"').replace(
                'skills = ["/project/skills/review"]', "skills = []"
            ),
        )
        bind(binary, "--url", server.url, "--token-env", "REVIEW_TOKEN", "--replace")
        plan = proposal(
            binary,
            path="/project/notes/a",
            flags=["--profile", "portable"],
            env={"REVIEW_TOKEN": "contract-admin"},
        )
        stored = save(binary, "plan.json", plan)
        refused = upkeep(
            binary,
            "apply",
            str(stored),
            "--write",
            "--approve",
            plan["id"],
            flags=["--profile", "portable"],
            env={"REVIEW_TOKEN": "reader-token"},
        )
        assert refused.returncode != 0
        assert (
            server.post("memory/read", scope=scope, path="notes/a")["document"][
                "version"
            ]
            == 1
        )
        first = server.request(
            "/v1/memory/search",
            {
                "scope": scope,
                "query": "needle",
                "knowledge_policy": "approved",
                "limit": 1,
            },
            token="reader-token",
        )
        cursor = first.body["next_cursor"]
        assert cursor
        for route in ("read", "versions", "history", "search"):
            result = server.request(
                "/v1/memory/" + route,
                {
                    "scope": "workspace:other",
                    "path": "notes/secret",
                    "query": "PRIVATE",
                    "actor": "client:contract",
                },
                token="reader-token",
            )
            assert result.status == 403 and "PRIVATE_ORG_DATA" not in str(result.body)
    text = grants.read_text()
    head, reader = text.split('actor = "client:reader"')
    grants.write_text(
        head + 'actor = "client:reader"' + reader.replace("read = true", "read = false")
    )
    with serve(binary, extra_env=env) as server:
        bind(binary, "--url", server.url, "--token-env", "REVIEW_TOKEN", "--replace")
        credentials = {"REVIEW_TOKEN": "reader-token"}
        for route in ("read", "versions", "history", "search"):
            result = server.request(
                "/v1/memory/" + route,
                {
                    "scope": scope,
                    "path": "notes/a",
                    "query": "needle",
                    "knowledge_policy": "approved",
                    "cursor": cursor,
                },
                token="reader-token",
            )
            assert result.status == 403
        cli_result = invoke(
            binary,
            "read",
            "/project/notes/a",
            flags=["--profile", "portable"],
            env=credentials,
        )
        assert cli_result.returncode == 1 and "needle" not in cli_result.stdout
        answers = rpc(
            binary,
            [
                ("knowledge", {"command": "read", "path": "/project/notes/a"}),
                ("search_memory", {"query": "needle", "knowledge_policy": "approved"}),
                ("memory_history", {"path": "/project/notes/a"}),
                ("knowledge", {"command": "references", "path": "/project/notes/a"}),
                (
                    "upkeep_apply",
                    {
                        "proposal": json.dumps(plan),
                        "write": True,
                        "approve": plan["id"],
                    },
                ),
            ],
            env=credentials,
        )
        assert all(a["result"]["isError"] for a in answers[2:])
        assert "needle" not in json.dumps(answers)


def test_older_search_service_cannot_silently_drop_review_policy(
    binary: Binary,
) -> None:
    def reply(path: str, _body: dict[str, Any]) -> tuple[int, Any]:
        assert path == "/v1/capabilities"
        return 200, {"wire_version": 6, "memory_search": {"version": 1}}

    with responder(reply) as (url, requests):
        result = run(
            binary,
            "memory",
            "search",
            "needle",
            "--scope",
            "org:test",
            "--url",
            url,
            "--knowledge-policy",
            "approved",
            "--json",
            env={"NEOSIAN_CLIENT_TOKEN": TOKEN},
        )
        assert result.returncode == 1 and "knowledge-policy version 1" in result.stdout
        assert requests == ["/v1/capabilities"]
