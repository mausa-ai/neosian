"""Retained behavior compared to frozen Python 1.13.0 results, never Rust."""

import ast
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.contract.replay.corpus import CASES, IDS
from tests.contract.replay.harness import (
    Case,
    Workspace,
    normalise,
    run_case,
    shape_of_output,
    snapshot,
    workspace,
)
from tests.contract.support import Binary

HERE = Path(__file__).parent
EXPECTED = json.loads((HERE / "expected.json").read_text())
SEED = json.loads((HERE / "seed.json").read_text())


def fresh_handoff(space: Workspace, keep: frozenset[str]) -> None:
    """Preserve the capture's fresh baton in the disposable copy only.

    The frozen oracle includes the full pending note, which becomes a one-line
    reminder after seven days. Move its body, envelope and version timestamps
    together; every nonvolatile byte must still match the frozen fixture.
    """
    scope = space.home / "user%3Areference/proj%3Ademo"
    document = scope / "documents/handoff.md"
    content = document.read_text()
    metadata = yaml.safe_load(content.split("---", 2)[1])
    written = next(
        line.removeprefix("- written: ")
        for line in content.splitlines()
        if line.startswith("- written: ")
    )
    delta = datetime.now(UTC) - timedelta(seconds=30) - datetime.fromisoformat(written)
    stamps = {written, metadata["created_at"], metadata["updated_at"]}
    for path in (document, scope / "versions/handoff.jsonl"):
        original = path.read_text()
        updated = original
        for stamp in stamps:
            shifted = (datetime.fromisoformat(stamp) + delta).isoformat()
            updated = updated.replace(stamp, shifted.replace("+00:00", "Z"))
        assert normalise(updated, space, keep=keep) == normalise(
            original, space, keep=keep
        )
        path.write_text(updated)


# S12 rewrote search_history for ranked results; the frozen comparison maps the
# description and parameter text back to version 1 (independent MCP checks pin
# the new text) and drops the new optional inputs.
HISTORY_V1 = {
    "description": "Search every recorded conversation for turns holding every given term (case-insensitive substrings, separated by spaces), newest first: message text, tool calls with their arguments, and tool results. Each hit names the conversation and turn, e.g. [cc-7f3a #2], with a snippet around the match; pass that id and number to recall_turn to read the turn verbatim. Use it when the memory index and the 'where we left off' block do not say where something happened, or when a fact lives in a session older than the ones shown. Pass conversation=<id> to search one session; limit is the newest N hits (default 10, at most 50).",
    "query": "One or more terms separated by spaces; a turn matches when it holds"
    " every one, case-insensitively.",
    "limit": "How many of the newest hits to return (default 10, at most 50).",
}


def retained(value: Any) -> Any:
    """Explicit v2 discovery additions; their full schemas have separate tests.

    The v1 responses and file deltas stay frozen. Version fields are checked
    against Cargo elsewhere, not an obsolete version literal.
    """
    if isinstance(value, dict):
        result = {key: retained(item) for key, item in value.items()}
        if "serverInfo" in result:
            result["serverInfo"]["version"] = "<VERSION>"
        if result.get("name") == "messages" and "inputSchema" in result:
            # S7's optional reference inputs have independent authority/codec tests.
            for field in ("context", "context_version", "change_id"):
                result["inputSchema"]["properties"].pop(field, None)
            result["description"] = result["description"].replace(
                "Optional context pins a mounted document and its current version. A change_id\n"
                "is a descriptive correlation, never proof or permission. Read references using\n"
                "current mounts; referenced content is not copied or executed.\n",
                "",
            )
        if result.get("name") == "search_history" and "inputSchema" in result:
            # S9/S12 search guidance and S12 ranking inputs have independent tests.
            result["description"] = HISTORY_V1["description"]
            properties = result["inputSchema"]["properties"]
            for field in ("order", "since", "before"):
                properties.pop(field, None)
            for field in ("query", "limit"):
                properties[field]["description"] = HISTORY_V1[field]
        if "wire_version" in result and "neosian_version" in result:
            result["neosian_version"] = "<VERSION>"
            result.pop(
                "memory_search", None
            )  # S2's optional capability has independent contracts.
            result.pop(
                "memory_ranking", None
            )  # S10's optional ranking has independent contracts.
            result.pop(
                "memory_passages", None
            )  # S11's optional passages have independent contracts.
            result.pop(
                "history_ranking", None
            )  # S12's optional ranking has independent contracts.
            result.pop("change_feed", None)  # S7 has independent durability contracts.
            result.pop(
                "knowledge_policy", None
            )  # S6's optional filter is tested separately.
        if "path_executables" in result and "update_mode" in result:
            result["version"] = "<VERSION>"
            result.pop("providers", None)  # v2 retires provider credential inspection.
            result.pop("installation", None)  # Independently checked setup diagnostics.
        if "mcp_registered" in result and "hooks_present" in result:
            result.pop("registrations", None)  # Retain every original status finding.
            if result.get("client") == "codex":
                # Native skill diagnostics have independent setup/discovery contracts.
                result.pop("skills", None)
            result.pop(
                "cache", None
            )  # Claude's additive setup metadata is tested separately.
        for key, added in (
            (
                "tools",
                {
                    "docs",
                    "memory_history",
                    "search_memory",
                    "upkeep_review",
                    "upkeep_apply",
                    "knowledge",
                    "changes",
                },
            ),
            ("prompts", {"upkeep"}),
        ):
            if isinstance(result.get(key), list):
                result[key] = [
                    item for item in result[key] if item.get("name") not in added
                ]
        return result
    if isinstance(value, (tuple, list)):
        return [retained(item) for item in value]
    if isinstance(value, str) and value.startswith("[{"):
        try:
            entries = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return value
        if isinstance(entries, list) and all(
            isinstance(row, dict)
            and "name" in row
            and ("description" in row or "error" in row)
            for row in entries
        ):
            return [row for row in entries if row["name"] != "upkeep"]
    if isinstance(value, str) and value.startswith("Available skills:"):
        return value.removesuffix(", upkeep")
    return value


def newest_call(request: Mapping[str, Any]) -> Mapping[str, Any]:
    """A JSON-RPC search_history call asking for version 1's newest order."""
    params = request.get("params")
    if request.get("method") != "tools/call" or not isinstance(params, Mapping):
        return request
    if params.get("name") != "search_history":
        return request
    arguments = {**params.get("arguments", {}), "order": "newest"}
    return {**request, "params": {**params, "arguments": arguments}}


def newest(case: Case) -> Case:
    """S12 made relevance the agent doors' default. The frozen history cases ask
    for the newest order explicitly, so every expected byte still compares."""
    argv = (
        case.argv + ("--order", "newest") if case.argv[:1] == ("search",) else case.argv
    )
    rpc = tuple(newest_call(request) for request in case.rpc)
    http = tuple(
        (
            replace(exchange, body=newest_call(exchange.body))
            if exchange.path == "/mcp" and isinstance(exchange.body, Mapping)
            else exchange
        )
        for exchange in case.http
    )
    return replace(case, argv=argv, rpc=rpc, http=http)


@pytest.mark.parametrize(
    "case",
    [case for case in CASES if case.name in EXPECTED],
    ids=lambda case: case.name,
)
def test_v1_reference(case: Case, binary: Binary, tmp_path: Path) -> None:
    keep = frozenset(SEED["sessions"]) | IDS
    space = workspace(
        HERE / "home",
        tmp_path / "reference",
        user=SEED["user"],
        proj=SEED["proj"],
        session=SEED["sessions"][-1],
        project_name="demo",
    )
    # Context surfaces need a fresh baton; audit/listing cases retain the
    # original chronology and continue to compare the untouched seed.
    if case.verb in {"record", "mcp"} or any(
        exchange.path == "/mcp" for exchange in case.http
    ):
        fresh_handoff(space, keep)
    for name in SEED["locks"]:
        lock = space.home / name
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_bytes(b"\0")
    if case.name in {"prune no match text", "prune dry run json"}:
        # Preserve the captured no-match conditions as the frozen seed ages.
        # Only the duration input changes; fixture and expected bytes stay exact.
        oldest = min(
            datetime.fromisoformat(json.loads(line)["created_at"])
            for path in (space.home / "conversations").glob("*/turns.jsonl")
            for line in path.read_text().splitlines()
        )
        age = datetime.now(UTC) - oldest
        hours = max(0, age.days * 24 + age.seconds // 3600) + 12
        at = case.argv.index("--older-than") + 1
        argv = (*case.argv[:at], f"{hours}h", *case.argv[at + 1 :])
        case = replace(case, argv=argv)
    # The reference capture had one PATH command installed. Supply that same
    # condition explicitly instead of inheriting the workstation's PATH.
    path_command = space.out / "status-bin" / "neosian"
    if case.name == "status json":
        path_command.parent.mkdir(parents=True)
        path_command.write_bytes(b"")
        path_command.chmod(0o700)
        case = replace(case, env={**case.env, "PATH": str(path_command.parent)})
    case = newest(case)
    before = snapshot(space, keep=keep)
    result = run_case(binary.executable, case, space)
    after = snapshot(space, keep=keep)
    observed = {
        "output": shape_of_output(case, result, space, keep=keep),
        "changed": {
            key: value
            for key, value in after.items()
            if key not in before or before[key] != value
        },
        "removed": sorted(set(before) - set(after)),
    }
    # JSON removes Python tuples; content and every nonvolatile field stay exact.
    expected = json.loads(json.dumps(EXPECTED[case.name]))
    if case.name == "status json":
        # Only the machine-specific location changes, with a one-to-one mapping.
        # Preserve the frozen count/order/name and every other status field.
        paths = expected["output"][1][0]["path_executables"]
        assert len(paths) == 1 and Path(paths[0]).name == "neosian"
        expected["output"][1][0]["path_executables"] = ["<OUT>/status-bin/neosian"]
    assert retained(json.loads(json.dumps(observed))) == retained(expected)
