"""Refuse a wrong artifact/tag; skip an already published, identical archive."""

import argparse
import base64
import hashlib
import json
import os
import re
import tarfile
import tomllib
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def metadata(path: Path, channel: str) -> tuple[str, str]:
    with tarfile.open(path) as archive:
        suffix = "/package.json" if channel == "npm" else "/Cargo.toml"
        members = [m for m in archive.getmembers() if m.name.endswith(suffix)]
        if len(members) != 1:
            raise ValueError("archive must have exactly one package manifest")
        manifest = archive.extractfile(members[0])
        if manifest is None:
            raise ValueError("missing package manifest")
        text = manifest.read().decode("utf-8")
    data = json.loads(text) if channel == "npm" else tomllib.loads(text)["package"]
    return str(data["name"]), str(data["version"])


def published(channel: str, version: str) -> dict[str, Any] | None:
    url = (
        f"https://registry.npmjs.org/neosian/{version}"
        if channel == "npm"
        else f"https://crates.io/api/v1/crates/neosian/{version}"
    )
    try:
        request = Request(url, headers={"User-Agent": "neosian-release/1"})
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
            if not isinstance(result, dict):
                raise ValueError("registry response is not an object")
            return result
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def needs_publish(path: Path, channel: str, tag: str) -> bool:
    name, version = metadata(path, channel)
    if name != "neosian" or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("only stable neosian artifacts may publish")
    if tag != f"v{version}":
        raise ValueError(f"artifact version {version} does not match tag {tag}")
    existing = published(channel, version)
    if existing is None:
        return True
    data = path.read_bytes()
    if channel == "npm":
        expected = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()
        actual = existing["dist"]["integrity"]
    else:
        expected = hashlib.sha256(data).hexdigest()
        actual = existing["version"]["checksum"]
    if actual != expected:
        raise ValueError(f"{channel} already has different bytes for {version}")
    return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("channel", choices=("npm", "crate"))
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    result = needs_publish(args.artifact, args.channel, os.environ["GITHUB_REF_NAME"])
    print(f"publish={str(result).lower()}")
