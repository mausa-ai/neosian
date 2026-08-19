"""Machine-readable schema export (DESIGN §5/§6).

`python -m neosian.schemas errors` prints the append-only error-code
registry as JSON, for host i18n-coverage tests.

`python -m neosian.schemas events [--out DIR]` prints the streaming-event
wire schemas (one per event plus the `agent_event` oneOf root), or writes
one `<name>.json` file each to DIR — hosts codegen their typed SSE seam
from them.
"""

import json
import sys
from pathlib import Path

from neosian._foundation.agent.events import event_schemas
from neosian._foundation.shared.exceptions import ERROR_CODES

_USAGE = "usage: python -m neosian.schemas {errors | events [--out DIR]}"


def _errors() -> int:
    registry = {
        code: {"exception": cls.__name__, "retryable": cls.default_retryable}
        for code, cls in sorted(ERROR_CODES.items())
    }
    print(json.dumps(registry, indent=2))
    return 0


def _events(out_dir: str | None) -> int:
    schemas = event_schemas()
    if out_dir is None:
        print(json.dumps(schemas, indent=2))
        return 0
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    for name, schema in schemas.items():
        (directory / f"{name}.json").write_text(json.dumps(schema, indent=2) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["errors"]:
        return _errors()
    if args == ["events"]:
        return _events(None)
    if len(args) == 3 and args[0] == "events" and args[1] == "--out":
        return _events(args[2])
    print(_USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
