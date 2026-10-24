"""Server grants remain authoritative over raw requests and edited profiles."""

from __future__ import annotations

import ast
import json
import tomllib
from typing import Any

import pytest

from tests.contract.profile_support import (
    GRANTS,
    PREFIX,
    PROFILE,
    bind,
    grant_env,
    install,
    rpc,
    run,
)
from tests.contract.schema import validate_schema
from tests.contract.support import ASSETS, LIMITED, Binary, serve


@pytest.mark.parametrize(
    "route,body",
    [
        ("memory/read", {"scope": "workspace:portable-adjacent", "path": "secret"}),
        ("memory/read", {"scope": "workspace:other", "path": "secret"}),
        ("memory/write", {"scope": "workspace:portable", "path": "x", "content": "no"}),
        ("memory/delete", {"scope": "workspace:portable", "path": "x"}),
        ("memory/rename", {"scope": "workspace:portable", "src": "x", "dst": "y"}),
        ("memory/redact", {"scope": "workspace:portable"}),
        ("conversation/read_turns", {"conversation_id": "other-session"}),
        ("conversation/read_projections", {"conversation_id": "other-session"}),
        ("conversation/append_turn", {"conversation_id": PREFIX + "s", "messages": []}),
        (
            "conversation/append_projections",
            {"conversation_id": PREFIX + "s", "entries": []},
        ),
        ("conversation/redact_turns", {"conversation_id": PREFIX + "s"}),
        ("conversation/search_turns", {"query": "secret"}),
        ("conversation/search_turns", {"query": "secret", "conversations": None}),
        (
            "conversation/search_turns",
            {"query": "secret", "conversations": [PREFIX + "s", "other-s"]},
        ),
        (
            "conversation/search_turns",
            {
                "query": "secret",
                "conversation_id": PREFIX + "s",
                "conversations": ["other-s"],
            },
        ),
        ("conversation/turn_redactions", {"conversation_id": PREFIX + "s"}),
        ("store/scopes", {}),
        ("store/conversations", {}),
        ("store/restore_scope", {"scope": "workspace:portable"}),
        ("store/restore_conversation", {"conversation_id": PREFIX + "s"}),
    ],
)
def test_readonly_grants_refuse_raw_escalation(
    binary: Binary, route: str, body: Any
) -> None:
    env = grant_env(binary.directory / "grants.toml")
    with serve(binary, extra_env=env) as server:
        reply = server.request("/v1/" + route, body, token="reader-token")
        assert reply.status == 403, reply
        assert reply.body["error"]["code"] == "forbidden"
        assert server.post("store/scopes") == {"scopes": []}
        assert server.post("store/conversations") == {"conversations": []}


def test_three_machines_share_knowledge_without_identity_or_account_assumptions(
    binary: Binary,
) -> None:
    validate_schema(
        json.loads((ASSETS / "grants-v1.schema.json").read_text()),
        tomllib.loads(GRANTS),
    )
    env = grant_env(binary.directory / "grants.toml")
    with serve(binary, extra_env=env) as server:
        server.post(
            "memory/write",
            scope="workspace:portable",
            path="decision",
            content="same knowledge",
        )
        server.post(
            "memory/write",
            scope="workspace:portable",
            path="skills/review",
            content="---\nname: review\ndescription: Portable review\n---\nRead the decision.",
        )
        server.post(
            "memory/write",
            scope="workspace:other",
            path="secret",
            content="UNAUTHORIZED",
        )
        for machine, agent, user in [
            ("work-checkout", "codex", "work-user"),
            ("home-projects", "claude-code", "home-user"),
            ("vps-service", "opencode", "service-user"),
        ]:
            directory = binary.directory / machine
            directory.mkdir()
            client = Binary(binary.executable, directory)
            install(client)
            bind(
                client,
                "--url",
                server.url,
                "--token-env",
                "WORKSPACE_TOKEN",
                "--project",
                str(directory),
            )
            credentials = {
                "WORKSPACE_TOKEN": "reader-token",
                "LOGNAME": user,
                "USER": user,
            }
            answers = rpc(
                client,
                [
                    ("memory", {"command": "view", "path": "/project/decision"}),
                    ("list_skills", {}),
                    (
                        "memory",
                        {"command": "create", "path": "/project/no", "content": "no"},
                    ),
                ],
                env=credentials,
                actor=f"mcp:{agent}",
            )
            assert "same knowledge" in answers[2]["result"]["content"][0]["text"]
            skill = ast.literal_eval(answers[3]["result"]["content"][0]["text"])[0]
            assert skill["scope"] == "workspace:portable" and skill["version"] == 1
            assert answers[4]["result"]["isError"] is True
            install(client, PROFILE.replace("workspace:portable", "workspace:other"))
            refused = run(
                client,
                "memory",
                "view",
                "/project/secret",
                "--profile",
                "portable",
                "--json",
                env=credentials,
            )
            assert refused.returncode == 1
            assert "UNAUTHORIZED" not in refused.stdout + refused.stderr
        capabilities = server.request(
            "/v1/capabilities", method="GET", token="reader-token"
        )
        assert capabilities.body["authorization"] == {"version": 1, "mode": "scoped"}
        assert (
            server.request(
                "/mcp",
                {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                token="reader-token",
            ).status
            == 403
        )


def test_history_filters_before_search_and_context_projection(binary: Binary) -> None:
    env = grant_env(binary.directory / "grants.toml")
    with serve(binary, extra_env=env) as server:
        for conversation, content in [
            (PREFIX + "allowed", "allowed history"),
            ("other-hidden", "UNAUTHORIZED history"),
        ]:
            server.post(
                "conversation/append_turn",
                conversation_id=conversation,
                messages=[{"role": "user", "content": content}],
            )
            server.post(
                "memory/write",
                scope="workspace:portable",
                path="sessions/" + conversation,
                content="session pointer",
            )
        install(
            binary,
            PROFILE
            + f'\n[history]\nsource = "knowledge"\nscope = "workspace:portable"\nprefix = "{PREFIX}"\n',
        )
        bind(binary, "--url", server.url, "--token-env", "WORKSPACE_TOKEN")
        answers = rpc(
            binary,
            [
                ("search_history", {"query": "history"}),
                ("recall_turn", {"turn": 1, "conversation": "other-hidden"}),
            ],
            env={"WORKSPACE_TOKEN": "reader-token"},
        )
        serialized = json.dumps(answers)
        assert "allowed history" in serialized
        assert "UNAUTHORIZED" not in serialized
        assert answers[3]["result"]["isError"] is True
        empty = server.request(
            "/v1/conversation/search_turns",
            {"query": "history", "conversations": []},
            token="reader-token",
        )
        assert empty.body == {"turns": []}


def test_profile_http_requires_scoped_authorization(binary: Binary) -> None:
    with serve(binary) as server:
        install(binary)
        bind(binary, "--url", server.url, "--token-env", "WORKSPACE_TOKEN")
        result = run(
            binary,
            "memory",
            "view",
            "/",
            "--profile",
            "portable",
            "--json",
            env={"WORKSPACE_TOKEN": "contract-admin"},
        )
        assert result.returncode == 1
        assert "scoped" in result.stdout + result.stderr


@pytest.mark.parametrize(
    "route", ["conversation/search_turns", "conversation/turn_redactions"]
)
@pytest.mark.parametrize(
    "extra", [{"scope": "user:allowed"}, {"conversation_id": "allowed-session"}]
)
def test_ignored_fields_cannot_authorize_legacy_global_reads(
    binary: Binary, route: str, extra: dict[str, str]
) -> None:
    with serve(binary) as server:
        response = server.request(
            "/v1/" + route, {"query": "secret", **extra}, token=LIMITED
        )
        assert response.status == 403


def test_grants_intersect_legacy_prefixes_and_revoke_on_restart(binary: Binary) -> None:
    path = binary.directory / "grants.toml"
    env = grant_env(path)
    env[
        "NEOSIAN_SERVE_TOKEN"
    ] += ",client:limited@workspace:portable-extra=limited-token"
    path.write_text(
        GRANTS + """
[[clients]]
actor = "client:limited"
[[clients.scopes]]
scope = "workspace:portable"
read = true
""",
        encoding="utf-8",
    )
    with serve(binary, extra_env=env) as server:
        assert (
            server.request(
                "/v1/memory/read",
                {"scope": "workspace:portable", "path": "x"},
                token="reader-token",
            ).status
            == 200
        )
        assert (
            server.request(
                "/v1/memory/read",
                {"scope": "workspace:portable", "path": "x"},
                token="limited-token",
            ).status
            == 403
        )
    path.write_text(
        GRANTS.split('[[clients]]\nactor = "client:reader"')[0], encoding="utf-8"
    )
    with serve(binary, extra_env=env) as server:
        assert (
            server.request(
                "/v1/memory/read",
                {"scope": "workspace:portable", "path": "x"},
                token="reader-token",
            ).status
            == 403
        )
