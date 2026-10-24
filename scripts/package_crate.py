"""Stage canonical data for cargo package/publish; never a second authored copy.

Require a clean source tree first, then run before `cargo package --locked
--allow-dirty` or `cargo publish --locked --allow-dirty`: Cargo counts the ignored
snapshot as uncommitted files. The crate's build script uses it only when the
repository's canonical assets are absent, as in a registry download.
"""

from pathlib import Path
from shutil import copy2, copytree, rmtree

ROOT = Path(__file__).resolve().parents[1]


def stage(root: Path = ROOT) -> None:
    crate = root / "crates" / "neosian"
    target = crate / "assets"
    if target.exists():
        rmtree(target)
    copytree(root / "assets", target, ignore=ignore_cache)
    copy2(root / "LICENSE", crate / "LICENSE")


def ignore_cache(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name == "__pycache__" or name.endswith(".pyc")}


if __name__ == "__main__":
    stage()
