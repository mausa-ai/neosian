"""Operator report projections. The engines retain all execution semantics."""

import json
import sys
from typing import Any

from rich.console import Console
from rich.text import Text

from neosian._cli.display import fields, note, records, section
from neosian._cli.render_status import render_providers
from neosian._foundation.llm.base import Usage
from neosian._foundation.shared.types import format_micro_usd


def render_configure(payload: dict[str, Any], console: Console) -> None:
    render_providers(payload, console)
    fields(console, [("Config", payload["config_path"])])


def render_setup(payload: dict[str, Any], console: Console) -> None:
    # Reuse the engine's outcome wording, including CLI-owned registrations.
    from neosian._cli.client_selection import discovery_text
    from neosian._cli.setup import _line

    written = payload["written"]
    section(console, "Setup results" if written else "Setup preview")
    fields(console, [("Level", payload["level"])])
    if searched := discovery_text(payload.get("searched", [])):
        console.print(Text(searched.rstrip()))
    for client in payload["clients"]:
        console.print()
        section(console, client["label"])
        records(
            console,
            ("Component", "Result"),
            [
                (
                    kind,
                    _line(kind, client[kind], write=written)
                    .strip()[len(kind) :]
                    .strip(),
                )
                for kind in ("mcp", "hooks")
            ],
        )
        for kind in ("mcp", "hooks"):
            result = client[kind]
            for key in ("note", "trust_hint", "hint"):
                if result.get(key):
                    note(console, str(result[key]))
            if shadow := result.get("mcp_shadowed_by"):
                sys.stderr.write(
                    f"note: preserved shared {shadow}; it overrides Muse's user registration\n"
                )
        if (files := client["hooks"].get("files")) and not written:
            section(console, "Hook files")
            # Configuration previews remain exact, copyable content.
            console.file.write(json.dumps(files, indent=2) + "\n")
    if not written:
        sys.stderr.write("hint: re-run with --write to apply\n")


def render_versions(payload: dict[str, Any], console: Console) -> None:
    section(console, "Version history")
    fields(console, [("Path", payload["path"])])
    if not payload["versions"]:
        console.print(Text(f"no history for {payload['path']}"))
        return
    records(
        console,
        ("Version", "Action", "Actor", "Updated", "State"),
        [
            (
                f"v{r['version']}",
                r["action"],
                r["actor"] or "-",
                r["created_at"],
                "redacted" if r["redacted"] else "available",
            )
            for r in payload["versions"]
        ],
    )


def render_maintenance(payload: dict[str, Any], console: Console) -> None:
    section(console, "Maintenance")
    if payload["writes"]:
        records(
            console,
            ("Action", "Path", "Version"),
            [
                (
                    r["command"],
                    r["path"],
                    "-" if r["version"] is None else f"v{r['version']}",
                )
                for r in payload["writes"]
            ],
        )
    else:
        console.print(Text("nothing to do"))
    if payload["usage"]:
        usage = payload["usage"]
        cost = payload["cost_micro_usd"]
        fields(
            console,
            [
                ("Model", payload["model"] or "-"),
                ("Tokens", str(Usage(**usage).total_tokens)),
                ("Cost", "unknown" if cost is None else format_micro_usd(cost)),
            ],
        )
    if payload["degraded"]:
        note(console, "Model stage failed; deterministic actions still landed.")


def render_transfer(payload: dict[str, Any], console: Console) -> None:
    section(console, payload["verb"].capitalize())
    fields(console, [("Archive", payload["archive"])])
    if not payload["units"]:
        console.print(Text(f"nothing to {payload['verb']}"))
        return
    rows = []
    for unit in payload["units"]:
        keys = (
            ("documents", "versions", "redactions")
            if unit["kind"] == "scope"
            else ("turns", "projections")
        )
        rows.append(
            (
                unit["kind"],
                unit["name"],
                ", ".join(
                    f"{unit[key]} {key[:-1] if unit[key] == 1 else key}" for key in keys
                ),
            )
        )
    records(console, ("Kind", "Name", "Transferred"), rows)


def render_erasure(payload: dict[str, Any], console: Console) -> None:
    section(console, payload["verb"].capitalize())
    if payload["verb"] == "redact":
        fields(
            console,
            [
                ("Conversation", payload["conversation_id"]),
                ("Turns redacted", str(payload["count"])),
            ],
        )
    else:
        fields(
            console,
            [
                ("Cutoff", payload["cutoff"]),
                ("Mode", "dry run" if payload["dry_run"] else "redacted"),
                ("Turns", str(payload["turns"])),
            ],
        )
        if payload["conversations"]:
            records(
                console,
                ("Conversation", "Turns", "Last turn"),
                [
                    (c["conversation_id"], str(c["turns"]), c["last_at"])
                    for c in payload["conversations"]
                ],
            )
        else:
            console.print(Text("nothing older than the cutoff"))
    if payload["documents"]:
        records(
            console,
            ("Scope", "Document"),
            [(d["scope"], f"/{d['path']}") for d in payload["documents"]],
        )
