"""`kind: memory` suite loader (DESIGN §13.12).

Same strict-key discipline as the agent loader; agent-kind keys fail
with targeted hints. Store errors raised by mount construction are
re-raised in the eval family — a `memory_*` code never escapes
`load_eval_config`.
"""

from collections.abc import Mapping
from typing import Any

from neosian._foundation.evaluation.cases import parse_script, parse_turns
from neosian._foundation.evaluation.memory_expectations import (
    parse_store_expectation,
)
from neosian._foundation.evaluation.memory_types import (
    MemoryEvalConfig,
    MemoryScenario,
    MemorySession,
    Transport,
)
from neosian._foundation.evaluation.schema import (
    check_keys,
    parse_models,
    parse_names,
    parse_stop_on_failure,
    parse_throttle_ms,
)
from neosian._foundation.memory.mounts import Mount
from neosian._foundation.shared.exceptions import (
    EvalCaseInvalidError,
    EvalConfigInvalidYAMLError,
    EvalConfigMissingKeyError,
    EvalConfigUnknownKeyError,
    MemoryStoreError,
)

_SUITE_KEYS = frozenset(
    {
        "kind",
        "name",
        "agent",
        "models",
        "mounts",
        "transports",
        "scenarios",
        "execute_tools",
        "ignore_tools",
        "stop_on_failure",
        "throttle_ms",
    }
)
_REQUIRED_KEYS = ("name", "agent", "models", "mounts", "scenarios")
_SUITE_HINTS = {
    "cases": "memory suites use 'scenarios:'",
    "variants": "memory suites compare 'transports:', not prompt variants",
}
_MOUNT_KEYS = frozenset({"scope", "mount_path", "read_only", "description"})
_SCENARIO_KEYS = frozenset({"name", "sessions"})
_SCENARIO_HINTS = {
    "input": "a memory scenario's turns live under 'sessions[].turns'",
    "conversation": "a memory scenario's turns live under 'sessions[].turns'",
}
_SESSION_KEYS = frozenset({"name", "turns", "script", "expect_store"})


def parse_memory_suite(data: Mapping[str, Any], path_str: str) -> MemoryEvalConfig:
    check_keys(
        data,
        allowed=_SUITE_KEYS,
        required=_REQUIRED_KEYS,
        path_str=path_str,
        hints=_SUITE_HINTS,
    )
    name = data["name"]
    agent = data["agent"]
    if not isinstance(name, str) or not isinstance(agent, str):
        raise EvalConfigInvalidYAMLError(path_str, "'name' and 'agent' must be strings")
    mounts = _parse_mounts(data["mounts"], path_str)
    mount_paths = frozenset(m.mount_path for m in mounts)
    return MemoryEvalConfig(
        name=name,
        agent=agent,
        models=parse_models(data["models"], path_str),
        mounts=mounts,
        scenarios=_parse_scenarios(data["scenarios"], mount_paths, path_str),
        transports=_parse_transports(data.get("transports"), path_str),
        execute_tools=parse_names(data.get("execute_tools"), "execute_tools", path_str),
        ignore_tools=parse_names(data.get("ignore_tools"), "ignore_tools", path_str),
        stop_on_failure=parse_stop_on_failure(data, path_str),
        throttle_ms=parse_throttle_ms(data, path_str),
    )


def _parse_mounts(data: Any, path_str: str) -> tuple[Mount, ...]:
    if not isinstance(data, list) or not data:
        raise EvalConfigInvalidYAMLError(path_str, "'mounts' must be a non-empty list")
    mounts: list[Mount] = []
    for idx, entry in enumerate(data, start=1):
        if not isinstance(entry, dict):
            raise EvalConfigInvalidYAMLError(path_str, f"mount {idx} must be a mapping")
        for key in entry:
            if key not in _MOUNT_KEYS:
                raise EvalConfigUnknownKeyError(f"mounts[{idx}].{key}", path_str)
        for key in ("scope", "mount_path"):
            if key not in entry:
                raise EvalConfigMissingKeyError(f"mounts[{idx}].{key}", path_str)
        scope, mount_path = entry["scope"], entry["mount_path"]
        read_only = entry.get("read_only", False)
        description = entry.get("description", "")
        if (
            not isinstance(scope, str)
            or not isinstance(mount_path, str)
            or not isinstance(read_only, bool)
            or not isinstance(description, str)
        ):
            raise EvalConfigInvalidYAMLError(
                path_str, f"mount {idx}: field of the wrong type"
            )
        try:
            mount = Mount(
                scope=scope,
                mount_path=mount_path,
                read_only=read_only,
                description=description,
            )
        except MemoryStoreError as e:
            raise EvalConfigInvalidYAMLError(
                path_str, f"mount {idx}: {e.message}"
            ) from e
        mounts.append(mount)
    paths = [m.mount_path for m in mounts]
    if len(set(paths)) != len(paths):
        raise EvalConfigInvalidYAMLError(path_str, "mount paths must be unique")
    return tuple(mounts)


def _parse_transports(data: Any, path_str: str) -> tuple[Transport, ...]:
    if data is None:
        return (Transport.FUNCTION,)
    if not isinstance(data, list) or not data:
        raise EvalConfigInvalidYAMLError(
            path_str, "'transports' must be a non-empty list"
        )
    transports: list[Transport] = []
    for entry in data:
        try:
            transport = Transport(entry)
        except ValueError:
            known = ", ".join(t.value for t in Transport)
            raise EvalConfigInvalidYAMLError(
                path_str, f"unknown transport {entry!r} — known transports: {known}"
            ) from None
        if transport in transports:
            raise EvalConfigInvalidYAMLError(
                path_str, f"duplicate transport '{transport.value}'"
            )
        transports.append(transport)
    return tuple(transports)


def _parse_scenarios(
    data: Any, mount_paths: frozenset[str], path_str: str
) -> tuple[MemoryScenario, ...]:
    if not isinstance(data, list) or not data:
        raise EvalConfigInvalidYAMLError(
            path_str, "'scenarios' must be a non-empty list"
        )
    scenarios: list[MemoryScenario] = []
    seen: set[str] = set()
    for entry in data:
        scenario = _parse_scenario(entry, mount_paths)
        if scenario.name in seen:
            raise EvalConfigInvalidYAMLError(
                path_str, f"duplicate scenario name '{scenario.name}'"
            )
        seen.add(scenario.name)
        scenarios.append(scenario)
    return tuple(scenarios)


def _parse_scenario(data: Any, mount_paths: frozenset[str]) -> MemoryScenario:
    if not isinstance(data, dict):
        raise EvalCaseInvalidError("unknown", "scenario must be a mapping")
    if "name" not in data or not isinstance(data["name"], str):
        raise EvalCaseInvalidError("unknown", "scenario missing 'name'")
    name = data["name"]
    for key in data:
        if key not in _SCENARIO_KEYS:
            hint = _SCENARIO_HINTS.get(str(key))
            raise EvalCaseInvalidError(
                name, f"unknown key '{key}'" + (f" — {hint}" if hint else "")
            )
    sessions_data = data.get("sessions")
    if not isinstance(sessions_data, list) or not sessions_data:
        raise EvalCaseInvalidError(name, "'sessions' must be a non-empty list")
    sessions: list[MemorySession] = []
    seen: set[str] = set()
    for entry in sessions_data:
        session = _parse_session(entry, name, mount_paths)
        if session.name in seen:
            raise EvalCaseInvalidError(name, f"duplicate session name '{session.name}'")
        seen.add(session.name)
        sessions.append(session)
    scripted = [s.script is not None for s in sessions]
    if any(scripted) and not all(scripted):
        raise EvalCaseInvalidError(
            name,
            "'script' must appear on every session or none — a half-scripted "
            "scenario would mix keyless and real-API sessions in one cell",
        )
    return MemoryScenario(name=name, sessions=tuple(sessions))


def _parse_session(
    data: Any, scenario_name: str, mount_paths: frozenset[str]
) -> MemorySession:
    if not isinstance(data, dict):
        raise EvalCaseInvalidError(scenario_name, "session must be a mapping")
    if "name" not in data or not isinstance(data["name"], str):
        raise EvalCaseInvalidError(scenario_name, "session missing 'name'")
    label = f"{scenario_name} / {data['name']}"
    for key in data:
        if key not in _SESSION_KEYS:
            raise EvalCaseInvalidError(label, f"session: unknown key '{key}'")
    if "turns" not in data:
        raise EvalCaseInvalidError(label, "session missing 'turns'")
    return MemorySession(
        name=data["name"],
        turns=parse_turns(data["turns"], label, key="turns"),
        script=parse_script(data.get("script"), label),
        expect_store=parse_store_expectation(
            data.get("expect_store"), label, mount_paths
        ),
    )
