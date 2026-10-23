"""Install cargo-dist's npm package with only its archive URL redirected locally.

Requires the host artifacts, Node and npm. npm resolves the generated package's
locked dependencies from the registry; the executable is the local release build.
"""

import functools
import http.server
import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check() -> None:
    artifacts = ROOT / "target/distrib"
    npm = shutil.which("npm")
    assert npm
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(artifacts)
    )
    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="neosian-npm-") as directory:
                work = Path(directory)
                subprocess.run(
                    [
                        "tar",
                        "xf",
                        str(artifacts / "neosian-npm-package.tar.gz"),
                        "-C",
                        str(work),
                    ],
                    check=True,
                )
                package = work / "package"
                manifest = package / "package.json"
                data = json.loads(manifest.read_text(encoding="utf-8"))
                version = data["version"]
                assert data["name"] == "neosian"
                data["artifactDownloadUrls"] = [
                    f"http://127.0.0.1:{server.server_port}"
                ]
                manifest.write_text(json.dumps(data), encoding="utf-8")
                env = {
                    **os.environ,
                    "HOME": str(work / "home"),
                    "NO_PROXY": "127.0.0.1",
                }
                Path(env["HOME"]).mkdir()
                packed = subprocess.check_output(
                    [
                        npm,
                        "pack",
                        str(package),
                        "--ignore-scripts",
                        "--json",
                        "--pack-destination",
                        str(work),
                        "--cache",
                        str(work / "cache"),
                    ],
                    env=env,
                    text=True,
                    timeout=60,
                )
                tarball = work / json.loads(packed)[0]["filename"]
                subprocess.run(
                    [
                        npm,
                        "install",
                        "--global",
                        "--prefix",
                        str(work / "prefix"),
                        "--cache",
                        str(work / "cache"),
                        "--no-audit",
                        "--no-fund",
                        str(tarball),
                    ],
                    env=env,
                    check=True,
                    timeout=120,
                )
                binary = work / "prefix/bin/neosian"
                output = subprocess.check_output(
                    [binary, "version"], env=env, text=True
                )
                assert output.strip() == f"neosian v{version}", output
                output = subprocess.check_output(
                    [binary, "docs", "--json"], env=env, text=True
                )
                topics = json.loads(output)["topics"]
                assert len(topics) == len(
                    list((ROOT / "neosian/assets/docs").glob("*.md"))
                )
                print(
                    f"npm global install runs neosian {version} and all embedded topics"
                )
        finally:
            server.shutdown()
            thread.join(timeout=5)


if __name__ == "__main__":
    check()
