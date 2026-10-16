#!/usr/bin/env python3
"""Push with a version bump: every push gets the next patch version (0.1.0 -> 0.1.1).

    scripts/ship.py                  # bump patch, commit "Version X.Y.Z", push
    scripts/ship.py minor            # 0.1.4 -> 0.2.0, then push
    scripts/ship.py major            # 0.2.3 -> 1.0.0, then push
    scripts/ship.py 2.0.0            # set an exact version, then push
    scripts/ship.py major --no-push  # bump and commit only
    scripts/ship.py --dry-run        # show what would happen

The version lives only in src/<package>/__init__.py (__version__); pyproject.toml reads it
(dynamic = ["version"], [tool.hatch.version]). A shields.io version badge in README.md
(`img.shields.io/badge/version-X.Y.Z-…`) is updated too if there is one. No tag is created:
tags (vX.Y.Z) start a release build, so tag by hand when you want a release. Commit your work
first; the bump commit only contains the version files (and README.md for the badge). If a push
fails, running this again reuses the bump commit instead of bumping twice. Standard library
only; no sudo, no network except `git push`.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
# The README badge: whatever version it shows (it may be stale) is replaced.
BADGE = re.compile(r"(img\.shields\.io/badge/version-)\d+\.\d+\.\d+(-)")
PARTS = ("patch", "minor", "major")


class ShipError(Exception):
    pass


def bumped(version: str, part: str) -> str:
    """The next version: `part` is patch/minor/major, or an exact X.Y.Z (must be higher)."""
    m = SEMVER.match(version)
    if not m:
        raise ShipError(f"current version {version!r} is not X.Y.Z")
    major, minor, patch = map(int, m.groups())
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "major":
        return f"{major + 1}.0.0"
    exact = SEMVER.match(part)
    if not exact:
        raise ShipError(f"expected patch, minor, major or X.Y.Z, got {part!r}")
    if tuple(map(int, exact.groups())) <= (major, minor, patch):
        raise ShipError(f"{part} is not higher than the current version {version}")
    return part


def version_file(root: Path) -> Path:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if "version" in project:
        raise ShipError('pyproject.toml sets version: use dynamic = ["version"] instead')
    return root / "src" / project["name"].replace("-", "_") / "__init__.py"


def read_version(root: Path) -> str:
    init = version_file(root)
    m = re.search(r'^__version__ = "([^"]+)"$', init.read_text(encoding="utf-8"), re.M)
    if not m:
        raise ShipError(f"no __version__ in {init.relative_to(root)}")
    return m.group(1)


def _replace_once(path: Path, pattern: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    out, n = re.subn(pattern, new, text, count=1, flags=re.M)
    if n != 1:
        raise ShipError(f"version line not found in {path.name}")
    path.write_text(out, encoding="utf-8")


def write_version(root: Path, old: str, new: str) -> None:
    init = version_file(root)
    _replace_once(init, rf'^__version__ = "{re.escape(old)}"$', f'__version__ = "{new}"')


def write_badge(root: Path, new: str) -> Path | None:
    """Set the README version badge to `new`; the README if it changed (no badge: None)."""
    readme = root / "README.md"
    if not readme.is_file():
        return None
    text = readme.read_text(encoding="utf-8")
    out = BADGE.sub(lambda m: f"{m.group(1)}{new}{m.group(2)}", text, count=1)
    if out == text:
        return None
    readme.write_text(out, encoding="utf-8")
    return readme


def git(root: Path, *args: str, check: bool = True) -> str:
    res = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)  # noqa: S603, S607
    if check and res.returncode != 0:
        raise ShipError(f"git {' '.join(args)} failed: {res.stderr.strip() or res.stdout.strip()}")
    return res.stdout.strip() if res.returncode == 0 else ""


def ship(root: Path, part: str = "patch", push: bool = True, dry_run: bool = False) -> str:
    """Bump (unless an unpushed bump commit is already on top), commit, push. Returns a summary."""
    if git(root, "status", "--porcelain", "--untracked-files=no"):
        raise ShipError(
            "uncommitted changes: commit them first (the bump commit holds only the version)"
        )
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        raise ShipError("detached HEAD: check out a branch first")
    upstream = git(root, "rev-parse", "--abbrev-ref", "@{u}", check=False)
    pending = int(git(root, "rev-list", "--count", f"{upstream}..HEAD")) if upstream else None
    current = read_version(root)
    # A failed push left our bump on top: push it as is (an explicit minor/major still bumps).
    last = git(root, "log", "-1", "--format=%s")
    reuse = part == "patch" and bool(pending) and last == f"Version {current}"
    if pending == 0 and part == "patch" and push:
        return "Nothing to push: no new commits, so no version bump."
    new = current if reuse else bumped(current, part)
    steps = [
        f"reuse the unpushed bump commit for {current}" if reuse else f"bump {current} -> {new}"
    ]
    if push:
        steps.append(f"push {branch}" + ("" if upstream else " (and set its upstream to origin)"))
    if dry_run:
        return "Would " + ", then ".join(steps) + "."
    if not reuse:
        write_version(root, current, new)
        changed = [version_file(root), write_badge(root, new)]
        files = [str(p.relative_to(root)) for p in changed if p is not None]
        git(root, "commit", "-m", f"Version {new}", "--", *files)
    if push:
        git(root, "push", *([] if upstream else ["-u"]), "origin", branch)
    return ("Pushed" if push else "Committed") + f" version {new} on {branch} (no tag)."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "part", nargs="?", default="patch", help="patch (default), minor, major or X.Y.Z"
    )
    parser.add_argument("--no-push", action="store_true", help="bump and commit only")
    parser.add_argument(
        "--dry-run", action="store_true", help="show what would happen, change nothing"
    )
    args = parser.parse_args(argv)
    try:
        print(ship(ROOT, args.part, push=not args.no_push, dry_run=args.dry_run))
    except ShipError as e:
        print(f"ship: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
