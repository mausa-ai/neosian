"""Validate published schemas offline, with no schema generation from code."""

from __future__ import annotations

import json
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from referencing import Registry, Resource

from tests.contract.support import ASSETS

STATE: dict[str, Any] = json.loads(
    (ASSETS / "state-v1.schema.json").read_text(encoding="utf-8")
)
OPENAPI: dict[str, Any] = json.loads(
    (ASSETS / "wire-v6.openapi.json").read_text(encoding="utf-8")
)
REGISTRY: Registry[Any] = Registry().with_resource(
    STATE["$id"], Resource.from_contents(STATE)
)
CHANGES: dict[str, Any] = json.loads(
    (ASSETS / "changes-v1.schema.json").read_text(encoding="utf-8")
)
REGISTRY = REGISTRY.with_resource(CHANGES["$id"], Resource.from_contents(CHANGES))


def validate(name: str, value: Any) -> None:
    schema = {"$ref": STATE["$id"] + "#/$defs/" + name}
    Draft202012Validator(schema, registry=REGISTRY).validate(value)


def validate_response(route: str, body: Any) -> None:
    schema = OPENAPI["paths"]["/v1/" + route]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    validate_schema(schema, body)


def validate_request(route: str, body: Any) -> None:
    schema = OPENAPI["paths"]["/v1/" + route]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    validate_schema(schema, body)


def validate_schema(schema: dict[str, Any], body: Any) -> None:
    # Resolve relative references against the published bundle without networking.
    Draft202012Validator(
        {"$id": "https://neosian.com/contracts/response", **schema}, registry=REGISTRY
    ).validate(body)
