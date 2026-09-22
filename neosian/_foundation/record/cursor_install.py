"""Cursor's flat version-1 hooks, validated before any displacement."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final, TextIO

from neosian._foundation.record.install import merge_hooks, strip_hooks
from neosian._foundation.record.targets import (
    HookTarget,
    installed_argv,
    resolve_target,
)
from neosian._foundation.shared.client_config import (
    FIX_BY_HAND,
    Environment,
    InstallError,
    ensure_evidence,
    load_document,
    write_text,
)
from neosian._foundation.shared.fileio import private_mkdir

EVENTS: Final = (
    "sessionStart",
    "beforeSubmitPrompt",
    "postToolUse",
    "afterAgentResponse",
    "stop",
)


def fragment(command: str) -> dict[str, Any]:
    return {"version": 1, "hooks": {e: [{"command": command}] for e in EVENTS}}


def _write(path: Path, document: dict[str, Any]) -> None:
    # Cursor 2026.09.10 strips // comments even inside JSON strings. Standard
    # escaped slashes keep daemon URLs intact through that reader.
    write_text(path, json.dumps(document, indent=2).replace("/", r"\/") + "\n")


def _document(path: Path, command: str) -> dict[str, Any]:
    document = load_document(path)
    if path.exists() and (
        type(document.get("version")) is not int or document["version"] != 1
    ):
        raise InstallError(f"{path} requires Cursor hooks version 1", FIX_BY_HAND)
    # Validate every event, including unrelated ones, before changing a file.
    hooks = document.get("hooks", {})
    if not isinstance(hooks, dict) or any(
        not isinstance(groups, list)
        or any(
            not isinstance(g, dict)
            or not (
                isinstance(g.get("command"), str)
                or (g.get("type") == "prompt" and isinstance(g.get("prompt"), str))
            )
            for g in groups
        )
        for groups in hooks.values()
    ):
        raise InstallError(f"invalid Cursor hooks in {path}", FIX_BY_HAND)
    return {**merge_hooks(document, fragment(command), path=path), "version": 1}


def run_install(
    target: HookTarget,
    context: Environment,
    *,
    command: str,
    write: bool,
    json_output: bool,
    out: TextIO,
    err: TextIO,
) -> int:
    other = resolve_target(
        "cursor", context, "project" if target.level == "user" else "user"
    )
    displaced: str | None = None
    try:
        ensure_evidence(target.label, target.evidence_dir)
        document = _document(target.config_path, command)
        if other.config_path != target.config_path:
            _document(other.config_path, command)
            if installed_argv(other) is not None:
                if target.level == "project":
                    raise InstallError(
                        f"Cursor already carries neosian hooks in {other.config_path}; "
                        "both would fire",
                        "keep the user hooks or remove them by hand",
                    )
                displaced = str(other.config_path)
        created = not target.config_path.exists()
        if write:
            private_mkdir(target.config_path.parent)
            _write(target.config_path, document)
            if displaced:
                _write(other.config_path, strip_hooks(load_document(other.config_path)))
    except (InstallError, OSError) as exc:
        hint = exc.hint if isinstance(exc, InstallError) else FIX_BY_HAND
        payload: dict[str, Any] = {
            "success": False,
            "client": "cursor",
            "config_path": str(target.config_path),
            "error": str(exc),
            "hint": hint,
        }
        if json_output:
            out.write(json.dumps(payload) + "\n")
        else:
            err.write(f"error: {exc}\nhint: {hint}\n")
        return 1
    payload = {
        "success": True,
        "client": "cursor",
        "label": "Cursor",
        "level": target.level,
        "config_path": str(target.config_path),
        "command": command,
        "hooks": fragment(command)["hooks"],
        "plugin": None,
        "written": write,
        "created": created,
        "displaced": displaced,
    }
    if json_output:
        out.write(json.dumps(payload) + "\n")
    elif write:
        out.write(f"{'created' if created else 'updated'} {target.config_path}\n")
    else:
        out.write(json.dumps(fragment(command), indent=2).replace("/", r"\/") + "\n")
        err.write(f"Cursor: {target.scope_note}; target {target.config_path}\n")
    if target.trust_hint:
        err.write(f"hint: {target.trust_hint}\n")
    if displaced:
        err.write(
            f"{'removed' if write else 'would remove'} neosian hooks from {displaced}\n"
        )
    return 0
