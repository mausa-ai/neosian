"""File-size gate: warn at 300 lines, fail at 500 (DESIGN §10).

Scope is library code only (neosian/**/*.py) — table-driven tests grow
legitimately. Allowlist entries carry a mandatory reason; an entry whose file
is gone or back under the limit is stale and fails the gate, so the list can
only shrink honestly.
"""

import sys
from pathlib import Path

WARN_LINES = 300
FAIL_LINES = 500
ROOT = Path(__file__).parents[1]

ALLOWLIST: dict[str, str] = {
    # path (relative to repo root) -> reason (mandatory)
    "neosian/_foundation/agent/base.py": "split lands in N0 (session-twin collapse)",
    "neosian/_cli/playground.py": "CLI monolith; migrates onto Conversation in N2",
    "neosian/_foundation/shared/types.py": "NS §4 usage/pricing rework restructures it",
    "neosian/_foundation/llm/anthropic.py": (
        "grew with v0.49 multimodal; single-file adapter until a real seam appears"
    ),
    "neosian/_foundation/shared/exceptions.py": "NS §5 error-code rework rewrites it",
}


def main() -> int:
    failures: list[str] = []
    seen_over_limit: set[str] = set()

    for path in sorted((ROOT / "neosian").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        lines = len(path.read_text().splitlines())
        if lines >= FAIL_LINES:
            seen_over_limit.add(rel)
            if rel in ALLOWLIST:
                print(f"ALLOW {rel}: {lines} lines ({ALLOWLIST[rel]})")
            else:
                failures.append(f"FAIL  {rel}: {lines} lines (limit {FAIL_LINES})")
        elif lines >= WARN_LINES:
            print(f"WARN  {rel}: {lines} lines (warn at {WARN_LINES})")

    for rel in sorted(set(ALLOWLIST) - seen_over_limit):
        failures.append(f"STALE {rel}: allowlisted but under {FAIL_LINES} lines")

    for failure in failures:
        print(failure)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
