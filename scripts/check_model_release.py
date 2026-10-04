"""Check the dated catalog before tagging; never mutate release state."""

import sys
from datetime import UTC, datetime
from pathlib import Path

from neosian import __version__
from neosian._foundation.shared.model_lifecycle import release_errors


def main() -> int:
    today = datetime.now(UTC).date()
    errors = release_errors(version=__version__, on=today)
    changelog = Path("docs/CHANGELOG.md").read_text(encoding="utf-8")
    if f"## [{__version__}] - {today}" not in changelog:
        errors.append(f"Date the {__version__} changelog section {today} UTC")
    for error in errors:
        print(f"refusing: {error}", file=sys.stderr)
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
