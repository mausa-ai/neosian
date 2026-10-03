"""The machine report, grouped for a human without discarding findings."""

from typing import Any

from rich.console import Console
from rich.text import Text

from neosian._cli.display import fields, note, records, section


def render_providers(status: dict[str, Any], console: Console) -> None:
    section(console, "Providers")
    records(
        console,
        ("Provider", "Key", "Source", "Environment"),
        [
            (
                p["name"],
                "configured" if p["source"] else "missing",
                p["source"] or "-",
                p["env"],
            )
            for p in status["providers"]
        ],
    )
    if not any(p["source"] for p in status["providers"]):
        console.print(Text("No keys configured — neosian configure", style="dim"))


def render_status(status: dict[str, Any], console: Console) -> None:
    section(console, "Installation")
    fields(
        console,
        [
            ("neosian", f"{status['version']} ({status['shape']})"),
            (
                "Home",
                f"{status['home']} ({'exists' if status['home_exists'] else 'missing'})",
            ),
            (
                "Config",
                f"{status['config_path']} "
                f"({status['config_error'] or ('exists' if status['config_exists'] else 'missing')})",
            ),
            ("Upgrade", status["upgrade"]),
            ("Update", f"mode {status['update_mode']}"),
        ],
    )
    console.print()
    render_providers(status, console)
    console.print()
    section(console, "Scopes")
    if status["scopes"]:
        fields(console, list(status["scopes"].items()))
    else:
        console.print(Text(status["scopes_error"] or "No scopes available"))
    console.print()
    section(console, "Clients")
    rows = []
    findings = []
    for c in status["clients"]:
        if not c["installed"] and c.get("searched_directory"):
            findings.append(f"{c['client']}: searched {c['searched_directory']}")
        interpreter = c["interpreter_resolves"]
        rows.append(
            (
                c["client"],
                "yes" if c["installed"] else "no",
                "registered" if c["mcp_registered"] else "missing",
                "present" if c["hooks_present"] else "missing",
                c["level"] or "-",
                "-" if interpreter is None else "ok" if interpreter else "missing",
            )
        )
        if interpreter is False:
            findings.append(f"{c['client']}: interpreter missing: {c['interpreter']}")
        if shadow := c.get("mcp_shadowed_by"):
            findings.append(f"{c['client']}: user MCP overridden by shared {shadow}")
        if delivery := c.get("message_delivery"):
            findings.append(f"{c['client']}: messages {', '.join(delivery)}")
    records(
        console, ("client", "installed", "mcp", "hooks", "level", "interpreter"), rows
    )
    console.print()
    section(console, "Last session")
    session = status["last_session"]
    if session:
        fields(
            console,
            [
                ("Agent", session["agent"]),
                ("Conversation", session["conversation"]),
                ("Updated", session["updated_at"]),
                ("Document", f"/project/{session['path']}"),
            ],
        )
    else:
        console.print(Text("none recorded"))
    if status["config_error"]:
        findings.append(f"Config: {status['config_error']}")
    if status["scopes_error"]:
        findings.append(f"Scopes: {status['scopes_error']}")
    findings.extend((*status["double_fire"], *status["one_writer"]))
    if findings:
        console.print()
        section(console, "Findings")
        for finding in findings:
            note(console, finding)
