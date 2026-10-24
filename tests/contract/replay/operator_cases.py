"""Operator reports, refusals and memory commands over the frozen wire."""

from __future__ import annotations

from tests.contract.replay.harness import Case

ROOT = ("--root", "{home}")


def operator_cases() -> list[Case]:
    audit = ("audit", *ROOT, "--scope", "{proj}")
    redact = ("redact", "{session}", *ROOT)
    cases = [
        Case("audit merged text", (*audit, "--conversation", "{session}")),
        Case("audit merged json", (*audit, "--conversation", "{session}", "--json")),
        Case("audit opaque actor", (*audit, "--actor", "seed", "--json")),
        Case("audit actor prefix", (*audit, "--actor", "codex:{session}", "--json")),
        Case("audit zero limit", (*audit, "--limit", "0", "--json")),
        Case("audit bounded", (*audit, "--limit", "2")),
        Case("audit no entries", ("audit", *ROOT, "--scope", "user:empty")),
        Case("audit layout", ("audit", *ROOT, "--limit", "1", "--json")),
        Case(
            "audit env scope",
            ("audit", *ROOT, "--json"),
            env={"NEOSIAN_SCOPE": "{proj}"},
        ),
        Case(
            "audit since offset",
            (*audit, "--since", "2099-01-01T00:00:00+02:00", "--json"),
        ),
        Case("audit since naive", (*audit, "--since", "2026-01-01", "--json")),
        Case("audit since malformed", (*audit, "--since", "yesterday", "--json")),
        Case("audit negative limit", (*audit, "--limit", "-1", "--json")),
        Case("audit bad scope", ("audit", *ROOT, "--scope", "bad", "--json")),
        Case("audit bad conversation", (*audit, "--conversation", "bad/id", "--json")),
        Case("redact all text", (*redact, "--all"), writes=True),
        Case("redact all json", (*redact, "--all", "--json"), writes=True),
        Case("redact through", (*redact, "--through", "2", "--json"), writes=True),
        Case(
            "redact named deduplicated",
            (*redact, "--turns", "3,1,3", "--actor", "cli:eraser", "--json"),
            writes=True,
        ),
        Case("redact absent", ("redact", "absent", *ROOT, "--all", "--json")),
        Case("redact no selector", (*redact, "--json")),
        Case("redact two selectors", (*redact, "--all", "--through", "1", "--json")),
        Case("redact zero through", (*redact, "--through", "0", "--json")),
        Case("redact negative through", (*redact, "--through", "-1", "--json")),
        Case("redact bad numbers", (*redact, "--turns", "one,2", "--json")),
        Case("redact zero turn", (*redact, "--turns", "1,0", "--json")),
        Case("redact bad id", ("redact", "bad/id", *ROOT, "--all", "--json")),
        Case("redact bad actor", (*redact, "--all", "--actor", "bad", "--json")),
        Case("prune no match text", ("prune", *ROOT, "--older-than", "12h")),
        Case("prune bad duration", ("prune", *ROOT, "--older-than", "0d", "--json")),
        Case("export text", ("export", "{out}", *ROOT), writes=True),
        Case(
            "export relative text",
            ("export", "./archive", *ROOT, "--scope", "{proj}"),
            writes=True,
        ),
        Case("import relative missing", ("import", "./not-there", *ROOT, "--json")),
        Case(
            "export scope deduplicated",
            (
                "export",
                "{out}",
                *ROOT,
                "--scope",
                "{proj}",
                "--scope",
                "{proj}",
                "--json",
            ),
            writes=True,
        ),
        Case(
            "export conversation text",
            ("export", "{out}", *ROOT, "--conversation", "{session}"),
            writes=True,
        ),
        Case(
            "export invalid scope",
            ("export", "{out}", *ROOT, "--scope", "bad", "--json"),
        ),
        Case(
            "export invalid conversation",
            ("export", "{out}", *ROOT, "--conversation", "bad/id", "--json"),
        ),
        Case("import missing directory", ("import", "{out}", *ROOT, "--json")),
        Case("import occupied", ("import", "{home}", *ROOT, "--json")),
    ]
    for verb in ("audit", "export", "redact", "prune", "memory"):
        argv = {
            "audit": ("audit",),
            "export": ("export", "{out}"),
            "redact": ("redact", "{session}", "--all"),
            "prune": ("prune", "--older-than", "1d"),
            "memory": ("memory", "view", "/"),
        }[verb]
        for name, flags in (
            ("root url conflict", (*ROOT, "--url", "http://127.0.0.1:1")),
            ("schema without postgres", (*ROOT, "--schema", "public")),
            ("bad url", ("--url", "file:///tmp/store")),
            ("missing token", ("--url", "http://127.0.0.1:1")),
        ):
            cases.append(Case(f"{verb} {name}", (*argv, *flags, "--json")))
    return cases


def remote_memory_cases() -> list[Case]:
    mounts = (*ROOT, "--scope", "{proj}")
    return [
        Case(f"remote memory {name}", ("memory", *argv, *mounts, "--json"), writes=True)
        for name, argv in (
            ("view", ("view", "/memories/facts")),
            ("create", ("create", "/memories/new", "--content", "new")),
            (
                "replace",
                (
                    "str_replace",
                    "/memories/facts",
                    "--old-str",
                    "eu-west-1",
                    "--new-str",
                    "eu-west-2",
                ),
            ),
            (
                "insert",
                (
                    "insert",
                    "/memories/facts",
                    "--insert-line",
                    "0",
                    "--insert-text",
                    "first",
                ),
            ),
            ("rename", ("rename", "/memories/facts", "/memories/moved")),
            ("delete", ("delete", "/memories/facts")),
            ("versions", ("versions", "/memories/facts")),
            ("revert", ("revert", "/memories/facts", "--version", "2")),
            ("redact", ("redact", "/memories/facts")),
            ("redact scope", ("redact", "/memories", "--all")),
        )
    ]
