"""Value-blind credential injection for the external test suites (DESIGN §10).

Reads a credentials file, injects only the keys on the provider's allowlist,
prints key *names* only — never values — and runs that provider's suite:

    uv run python scripts/external_env.py --provider anthropic --file ~/keys/anthropic.txt

The file holds either env-style NAME=value lines or a single raw value (taken
as the provider's primary key). Without --file, keys already in the
environment are used as-is.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

KEY_ALLOWLIST: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "cerebras": ("CEREBRAS_API_KEY",),
    # The candidate doors (tests/external/candidates.py); first = primary.
    "xai": ("XAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "qwen": ("DASHSCOPE_API_KEY",),
    "kimi": ("MOONSHOT_API_KEY",),
}


def _parse_creds(path: Path, primary_key: str) -> dict[str, str]:
    """Parse NAME=value lines; a bare single value maps to the primary key."""
    text = path.read_text().strip()
    if "=" not in text:
        return {primary_key: text}
    creds: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, value = line.partition("=")
        creds[name.strip()] = value.strip()
    return creds


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, choices=sorted(KEY_ALLOWLIST))
    parser.add_argument("--file", type=Path, default=None)
    args = parser.parse_args()

    allowed = KEY_ALLOWLIST[args.provider]
    env = os.environ.copy()

    if args.file is not None:
        creds = _parse_creds(args.file, primary_key=allowed[0])
        for name in sorted(set(creds) - set(allowed)):
            print(f"ignored (not on {args.provider} allowlist): {name}")
        for name in allowed:
            if creds.get(name):
                env[name] = creds[name]
                print(f"injected: {name}")

    for name in allowed:
        if not env.get(name):
            print(f"absent: {name} (its tests will self-skip)")

    cmd = [sys.executable, "-m", "pytest", "-m", f"external_{args.provider}", "-v"]
    return subprocess.run(cmd, env=env, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
