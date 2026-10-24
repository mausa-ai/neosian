"""Required transport behavior and explicit refusals for optional capabilities."""

import sys

from tests.contract.storage.support import Storage, refused


def test_the_contract_tier_did_not_import_the_python_product() -> None:
    assert not any(
        name == "neosian" or name.startswith("neosian.") for name in sys.modules
    )


def test_authentication_precedes_body_decoding(storage: Storage) -> None:
    assert storage.server.request(
        "/health", method="GET", authenticated=False
    ).body == {"status": "ok"}
    denied = storage.server.request(
        "/v1/memory/read", authenticated=False, raw=b"not json"
    )
    assert (denied.status, denied.body) == (401, "unauthorized")
    assert denied.headers["www-authenticate"] == "Bearer"
    refused(storage.server.request("/v1/memory/read", raw=b"not json"), "value_error")
    refused(storage.server.request("/v1/memory/read", []), "value_error")


def test_undeclared_capabilities_refuse_their_operations(storage: Storage) -> None:
    if not storage.capabilities["erasable"]:
        for route, body in [
            ("redact_turns", {"conversation_id": storage.conversation}),
            ("turn_redactions", {"conversations": [storage.conversation]}),
        ]:
            refused(
                storage.server.request("/v1/conversation/" + route, body),
                "agent_configuration_error",
            )
    if not storage.portable:
        for route in [
            "scopes",
            "conversations",
            "restore_scope",
            "restore_conversation",
        ]:
            refused(
                storage.server.request("/v1/store/" + route, {}),
                "agent_configuration_error",
            )
    if not storage.capabilities["pageable"]:
        refused(
            storage.server.request(
                "/v1/memory/list_documents",
                {"scope": storage.scope, "cursor": "opaque"},
            ),
            "agent_configuration_error",
        )
