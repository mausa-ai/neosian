"""Mailbox argv cases: the CLI and MCP consume the same command service."""

from tests.differential.harness import Case


def message_cases() -> tuple[Case, ...]:
    mounts = ("--root", "{home}", "--mount", "scope={proj},path=project")
    cases = (
        ("send", "--body", "A unicode reminder: café ✓", "--about", "{session}"),
        (
            "send",
            "--body",
            "future",
            "--due-at",
            "2035-01-01T12:00:00+02:00",
            "--delivery",
            "wake",
        ),
        ("send", "--body", ""),
        ("send", "--body", "x", "--due-at", "2035-01-01T12:00:00"),
        ("send", "--body", "x", "--delay-seconds", "-1"),
        ("send", "--body", "x", "--target", "/absent"),
        ("send", "--body", "x", "--conversation", "bad/id"),
        ("view", "--message-id", "absent"),
        ("view",),
        ("ack", "--message-id", "absent"),
        ("list", "--limit", "0"),
        ("list", "--status", "all", "--target", "project"),
        ("list", "--status", "closed"),
    )
    return tuple(
        Case(
            f"messages args {index} {'json' if flags else 'text'}",
            ("messages", *argv, *mounts, *flags),
        )
        for index, argv in enumerate(cases)
        for flags in ((), ("--json",))
    )
