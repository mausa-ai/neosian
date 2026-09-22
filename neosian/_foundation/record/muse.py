"""Muse hook installation: ordinary files, managed only for credentials.

All documents are validated before any write or displacement. The settings
file owns the managed pointer; a pre-existing foreign pointer is never moved.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TextIO

from neosian._foundation.record.install import hook_fragment, merge_hooks, strip_hooks
from neosian._foundation.record.settings import RecordSettings
from neosian._foundation.record.targets import active_targets, installed_argv
from neosian._foundation.shared.client_config import (
    FIX_BY_HAND,
    Environment,
    InstallError,
    ensure_evidence,
    load_document,
    write_document,
)
from neosian._foundation.shared.fileio import private_mkdir
from neosian._foundation.shared.muse_config import (
    config_dir,
    credential_names,
    managed_path,
    settings_document,
)


def documents(
    context: Environment, settings: RecordSettings, level: str, command: str
) -> tuple[Path, dict[Path, dict[str, Any]]]:
    """Target and complete proposed documents, with no filesystem mutation."""
    base = config_dir(context)
    ensure_evidence("Muse Code", base)
    user_path = base / "settings.json"
    user = settings_document(user_path)
    names = credential_names(settings.store)
    if names and level == "project":
        raise InstallError(
            "Muse credential-backed hooks require user level",
            "re-run with --level user: managed hooks are machine-wide",
        )
    owned = base / "neosian-hooks.json"
    managed = managed_path(user, user_path)
    if names and managed is not None and managed != owned:
        raise InstallError(
            f"Muse already uses a managed hooks file: {managed}",
            "merge the neosian hooks and required environment variable names "
            "into your managed configuration by hand; its pointer is preserved",
        )
    if "managed_hooks_path" in user and managed is None:
        raise InstallError(
            "Muse managed_hooks_path must be a nonempty path", FIX_BY_HAND
        )
    target = (
        (owned if names else user_path)
        if level == "user"
        else context.cwd / ".muse" / "hooks.json"
    )
    changes: dict[Path, dict[str, Any]] = {}
    # Managed and ordinary user handlers both count towards the one-level rule.
    for at in ("user", "project"):
        for source in active_targets("muse-code", context, at):
            path = source.config_path
            # Validate every active source even when its handlers are not ours.
            document = user if path == user_path else load_document(path)
            merge_hooks(document, hook_fragment(command), path=path)
            if path == target or installed_argv(source) is None:
                continue
            if level == "project" or (path == managed and path != owned):
                raise InstallError(
                    f"Muse already carries neosian hooks in {path}; both would fire",
                    "keep the user-level hooks, or remove those handlers by hand",
                )
            changes[path] = strip_hooks(document)
    document = user if target == user_path else load_document(target)
    changes[target] = merge_hooks(document, hook_fragment(command), path=target)
    if names:
        user = dict(changes.get(user_path, user))
        forwarded = user.get("managed_hooks_env_vars", [])
        if not isinstance(forwarded, list) or not all(
            isinstance(name, str) for name in forwarded
        ):
            raise InstallError(
                "Muse managed_hooks_env_vars must be a string array", FIX_BY_HAND
            )
        user["managed_hooks_path"] = owned.name
        user["managed_hooks_env_vars"] = list(dict.fromkeys([*forwarded, *names]))
        changes[user_path] = user
    return target, changes


def run_install(
    context: Environment,
    settings: RecordSettings,
    *,
    level: str,
    command: str,
    write: bool,
    json_output: bool,
    out: TextIO,
    err: TextIO,
) -> int:
    target = config_dir(context) / "settings.json"
    try:
        target, changes = documents(context, settings, level, command)
        created = not target.exists()
        if write:
            # Install the new source before pointing settings at it. Remove
            # old sources last; a failed write never deletes the old hooks first.
            for path, document in changes.items():
                if path == target:
                    private_mkdir(path.parent)
                    write_document(path, document)
            for path, document in changes.items():
                if path != target:
                    write_document(path, document)
    except (InstallError, OSError) as exc:
        error = (
            exc
            if isinstance(exc, InstallError)
            else InstallError(str(exc), FIX_BY_HAND)
        )
        payload: dict[str, Any] = {
            "success": False,
            "client": "muse-code",
            "config_path": str(target),
            "error": error.message,
            "hint": error.hint,
        }
        if json_output:
            out.write(json.dumps(payload) + "\n")
        else:
            err.write(f"error: {error.message}\nhint: {error.hint}\n")
        return 1
    payload = {
        "success": True,
        "client": "muse-code",
        "label": "Muse Code",
        "level": level,
        "config_path": str(target),
        "command": command,
        "hooks": hook_fragment(command)["hooks"],
        "plugin": None,
        "written": write,
        "created": created,
        "displaced": None,
        "files": _preview(
            changes,
            target,
            config_dir(context) / "settings.json",
            command,
            credential_names(settings.store),
        ),
    }
    if json_output:
        out.write(json.dumps(payload, ensure_ascii=False) + "\n")
    elif write:
        for path in changes:
            out.write(f"updated {path}\n")
    else:
        out.write(json.dumps(payload["files"], indent=2, ensure_ascii=False) + "\n")
        err.write("Muse Code: proposed files; re-run with --write to apply\n")
    if level == "project":
        err.write("hint: Muse loads project hooks only in a trusted workspace\n")
    return 0


def _preview(
    changes: dict[Path, dict[str, Any]],
    target: Path,
    user_path: Path,
    command: str,
    names: tuple[str, ...],
) -> dict[str, Any]:
    """Only our edits are printable; other settings may contain credentials."""
    files: dict[str, Any] = {}
    for path in changes:
        merge = hook_fragment(command) if path == target else {}
        change: dict[str, Any] = {
            "remove_neosian_hooks": path != target,
            "merge": merge,
        }
        if path == user_path:
            merge["schema_version"] = 1
            if names:
                merge["managed_hooks_path"] = target.name
                change["add_managed_hooks_env_vars"] = list(names)
        files[str(path)] = change
    return files
