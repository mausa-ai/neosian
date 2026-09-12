"""File-size gate: warn at 300 lines, fail at 500 (DESIGN §10).

Scope is library code only (neosian/**/*.py) — table-driven tests grow
legitimately. An allowlist entry is a named debt with a hard line: it carries
a ceiling and a reason, a file over its ceiling fails like any other, and an
entry whose file is gone or back under the limit is stale and fails the gate,
so the list can only shrink honestly.
"""

import sys
from pathlib import Path

WARN_LINES = 300
FAIL_LINES = 500
ROOT = Path(__file__).parents[1]

ALLOWLIST: dict[str, tuple[int, str]] = {
    # path (relative to repo root) -> (ceiling, reason); both mandatory.
    # Empty since NC7 — the registry is a package and the Anthropic adapter
    # has its seams; the mechanism stays for the next real one.
}


def main() -> int:
    failures: list[str] = []
    seen_over_limit: set[str] = set()

    for path in sorted((ROOT / "neosian").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines >= FAIL_LINES:
            seen_over_limit.add(rel)
            if rel not in ALLOWLIST:
                failures.append(f"FAIL  {rel}: {lines} lines (limit {FAIL_LINES})")
                continue
            ceiling, reason = ALLOWLIST[rel]
            if lines > ceiling:
                failures.append(f"FAIL  {rel}: {lines} lines (ceiling {ceiling})")
            else:
                print(f"ALLOW {rel}: {lines} lines (ceiling {ceiling}; {reason})")
        elif lines >= WARN_LINES:
            print(f"WARN  {rel}: {lines} lines (warn at {WARN_LINES})")

    for rel in sorted(set(ALLOWLIST) - seen_over_limit):
        failures.append(f"STALE {rel}: allowlisted but under {FAIL_LINES} lines")

    for failure in failures:
        print(failure)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
