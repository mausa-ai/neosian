"""Machine-readable schema export (DESIGN §5/§6).

`python -m neosian.schemas errors` prints the append-only error-code
registry as JSON, for host i18n-coverage tests. The `events` command
arrives with event schema v2 (N0).
"""

import json
import sys

from neosian._foundation.shared.exceptions import ERROR_CODES


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args != ["errors"]:
        print("usage: python -m neosian.schemas errors", file=sys.stderr)
        return 2
    registry = {
        code: {"exception": cls.__name__, "retryable": cls.default_retryable}
        for code, cls in sorted(ERROR_CODES.items())
    }
    print(json.dumps(registry, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
