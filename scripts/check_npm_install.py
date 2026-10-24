"""Install cargo-dist's npm package with only its archive URL redirected locally.

Requires the host artifacts, Node and npm. npm resolves the generated package's
locked dependencies from the registry; the executable is the local release build.
"""

import functools
import http.server
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def publication(work: Path, npm: str, env: dict[str, str], version: str) -> None:
    """Run the workflow's actual command in dry-run mode, including its path.

    npm treats a bare `artifacts/name.tar.gz` as GitHub shorthand. Installing
    an absolute tarball path does not exercise that publication failure.
    """
    workflow = (ROOT / ".github/workflows/publish-channels.yml").read_text()
    commands = [
        shlex.split(line.strip().removeprefix("run: "))
        for line in workflow.splitlines()
        if line.strip().startswith("run: npm publish ")
    ]
    assert len(commands) == 1
    command = commands[0]
    assert len(command) == 5 and command[:4] == ["npm", "publish", "--access", "public"]
    path = Path(command[-1])
    assert path == Path("artifacts/neosian-npm-package.tar.gz")
    (work / path).parent.mkdir()
    shutil.copy2(ROOT / "target/distrib" / path.name, work / path)
    subprocess.check_output(
        [
            npm,
            *command[1:],
            "--dry-run",
            "--ignore-scripts",
            "--cache",
            str(work / "cache"),
        ],
        cwd=work,
        env=env,
        text=True,
        timeout=120,
    )
    print(f"release workflow's npm publication command accepts neosian {version}")


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
                publication(work, npm, env, version)
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
                assert len(topics) == len(list((ROOT / "assets/docs").glob("*.md")))
                print(
                    f"npm global install runs neosian {version} and all embedded topics"
                )
        finally:
            server.shutdown()
            thread.join(timeout=5)


if __name__ == "__main__":
    check()
