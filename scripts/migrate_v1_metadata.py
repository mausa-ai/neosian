"""One-time v1 envelope conversion: preview by default, backups before --write.

Requires PyYAML, not the retired neosian Python product. Run with all writers
stopped. Only the envelope changes; the body and historical journals stay exact.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

import yaml

RESERVED = {
    "neosian_format",
    "version",
    "created_at",
    "updated_at",
    "actor",
    "redacted",
}


def json_keys(value: object) -> None:
    """JSON must not silently coerce a YAML mapping's non-string keys."""
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("metadata keys must be strings, including nested keys")
        for item in value.values():
            json_keys(item)
    elif isinstance(value, list):
        for item in value:
            json_keys(item)


def converted(raw: bytes) -> bytes:
    text = raw.decode("utf-8")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise ValueError("missing envelope fence")
    close = next(
        (n for n in range(1, len(lines)) if lines[n].rstrip("\r\n") == "---"), None
    )
    if close is None:
        raise ValueError("unterminated envelope fence")
    data = yaml.safe_load("".join(lines[1:close]))
    if (
        not isinstance(data, dict)
        or type(data.get("neosian_format")) is not int
        or data["neosian_format"] != 1
    ):
        raise ValueError("expected memory format 1")
    if type(data.get("version")) is not int or not 1 <= data["version"] < 2**64:
        raise ValueError("invalid document version")
    if data.get("actor") is not None and not isinstance(data["actor"], str):
        raise ValueError("actor must be a string or null")
    if "redacted" in data and type(data["redacted"]) is not bool:
        raise ValueError("redacted must be a boolean")
    for key in ("created_at", "updated_at"):
        stamp = data.get(key)
        if isinstance(stamp, str):
            stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if not isinstance(stamp, datetime) or stamp.tzinfo is None:
            raise ValueError(f"{key} must be a timezone-aware timestamp")
    try:
        json_keys(data)
    except RecursionError as error:
        raise ValueError(
            "recursive YAML aliases cannot be represented in JSON"
        ) from error
    header = []
    for key, value in data.items():
        if not isinstance(key, str):
            raise ValueError("metadata keys must be strings")
        if key in {"created_at", "updated_at"} and hasattr(value, "isoformat"):
            value = value.isoformat()
        try:
            spelled = json.dumps(
                value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            )
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"metadata key {key!r} is not JSON-compatible; convert it explicitly"
            ) from error
        # Reserved keys are scalar; JSON strings and collections are valid YAML.
        if key in RESERVED and isinstance(value, (list, dict)):
            raise ValueError(f"reserved key {key!r} must be a scalar")
        name = key if key in RESERVED else json.dumps(key, ensure_ascii=False)
        header.append(name + ": " + spelled + "\n")
    return ("---\n" + "".join(header) + "---\n" + "".join(lines[close + 1 :])).encode(
        "utf-8"
    )


def migrate(root: Path, write: bool) -> list[str]:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("root must be a FileStore directory")
    plan = []
    for path in sorted(root.rglob("documents/**/*.md")):
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError("refusing a symlink in the document path")
        original = path.read_bytes()
        changed = converted(original)
        if original != changed:
            backup = path.with_suffix(path.suffix + ".v1-backup")
            if write and backup.exists():
                raise ValueError(f"backup already exists: {backup}")
            plan.append((path, backup, original, changed))
    # Validate the entire selection before creating a backup or changing a file.
    if write:
        for path, backup, original, changed in plan:
            with backup.open("xb") as handle:
                os.chmod(backup, 0o600)
                handle.write(original)
                handle.flush()
                os.fsync(handle.fileno())
            descriptor, temporary = tempfile.mkstemp(
                prefix=".neosian-metadata-", dir=path.parent
            )
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(changed)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
    return [str(path.relative_to(root)) for path, *_ in plan]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    try:
        paths = migrate(args.root, args.write)
    except (OSError, ValueError, yaml.YAMLError) as error:
        parser.exit(1, f"migration refused: {error}\n")
    print(json.dumps({"applied": args.write, "documents": paths}))


if __name__ == "__main__":
    main()
