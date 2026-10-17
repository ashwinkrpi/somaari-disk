"""scripts/ship.py: patch bump per push, minor/major/exact on request, tags only with --release.

Runs against a throwaway git repo with a local bare "origin" in tmp_path: no network, and
this checkout is never touched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ship.py"
sys.path.insert(0, str(SCRIPT.parent))
import ship  # noqa: E402

GIT_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",  # with HOME=tmp: no system/user git config, hooks or signing
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "HOME": str(cwd), "USERPROFILE": str(cwd), **GIT_ENV}
    res = subprocess.run(  # noqa: S603 - git with test-controlled args
        ["git", *args],  # noqa: S607 - git from PATH, like the script under test
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return res.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A repo like this one (pyproject + src/<pkg>/__init__.py + scripts/ship.py), pushed once."""
    remote = tmp_path / "origin.git"
    work = tmp_path / "work"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    (work / "src" / "demo_app").mkdir(parents=True)
    (work / "scripts").mkdir()
    (work / "pyproject.toml").write_text(
        '[project]\nname = "demo-app"\ndynamic = ["version"]\n\n'
        '[tool.ruff]\ntarget-version = "py311"\n'
    )
    (work / "src" / "demo_app" / "__init__.py").write_text('"""Demo."""\n\n__version__ = "0.1.0"\n')
    shutil.copy(SCRIPT, work / "scripts" / "ship.py")
    git(work, "init", "-q", "-b", "main")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "init")
    git(work, "remote", "add", "origin", str(remote))
    git(work, "push", "-q", "-u", "origin", "main")
    return work


def run_ship(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(repo), "USERPROFILE": str(repo), **GIT_ENV}
    return subprocess.run(  # noqa: S603 - this repo's script copied into tmp_path
        [sys.executable, "scripts/ship.py", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )


def work_commit(repo: Path, name: str = "feature.txt") -> None:
    (repo / name).write_text("change\n")
    git(repo, "add", name)
    git(repo, "commit", "-qm", f"add {name}")


def versions(repo: Path) -> str:
    init = (repo / "src" / "demo_app" / "__init__.py").read_text(encoding="utf-8")
    return init.split('__version__ = "')[1].split('"')[0]


@pytest.mark.parametrize(
    ("version", "part", "new"),
    [
        ("0.1.0", "patch", "0.1.1"),
        ("0.1.9", "patch", "0.1.10"),
        ("0.1.4", "minor", "0.2.0"),
        ("0.2.3", "major", "1.0.0"),
        ("0.2.3", "2.0.0", "2.0.0"),
        ("1.2.3", "1.2.4", "1.2.4"),
    ],
)
def test_bumped(version, part, new):
    assert ship.bumped(version, part) == new


@pytest.mark.parametrize(
    ("version", "part"),
    [("0.1.0", "0.1.0"), ("0.2.0", "0.1.9"), ("0.1.0", "big"), ("0.1", "patch")],
)
def test_bumped_rejects(version, part):
    with pytest.raises(ship.ShipError):
        ship.bumped(version, part)


def test_patch_bump_per_push(repo):
    work_commit(repo)
    res = run_ship(repo)
    assert res.returncode == 0, res.stderr
    assert "Pushed version 0.1.1 on main (no tag)" in res.stdout
    assert versions(repo) == "0.1.1"
    assert git(repo, "log", "-1", "--format=%s") == "Version 0.1.1"
    assert git(repo, "rev-parse", "HEAD") == git(repo, "rev-parse", "origin/main")  # pushed
    assert git(repo, "tag") == ""  # tags start release builds: never automatic
    # the bump commit holds only the version file
    files = git(repo, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert sorted(files) == ["src/demo_app/__init__.py"]
    work_commit(repo, "second.txt")
    assert run_ship(repo).returncode == 0
    assert versions(repo) == "0.1.2"


BADGE = "[![Version](https://img.shields.io/badge/version-{}-61afef)](CHANGELOG.md)"


def test_readme_is_never_touched(repo):
    # the README badge shows the latest GitHub release (shields reads it), not __version__
    text = "# Demo, version 0.1.0\n\n" + BADGE.format("0.0.9") + "\n"
    (repo / "README.md").write_text(text)
    git(repo, "add", "README.md")
    git(repo, "commit", "-qm", "readme")
    assert run_ship(repo).returncode == 0
    assert (repo / "README.md").read_text(encoding="utf-8") == text
    assert "README.md" not in git(repo, "show", "--name-only", "--format=", "HEAD")


def test_this_repo_readme_badge_follows_releases():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "img.shields.io/badge/version-" not in readme  # a static badge would go stale
    assert "img.shields.io/github/v/release/" in readme


def test_release_tags_and_pushes_the_bump(repo):
    work_commit(repo)
    res = run_ship(repo, "--release")
    assert res.returncode == 0, res.stderr
    assert "gh release edit v0.1.1 --draft=false" in res.stdout
    assert versions(repo) == "0.1.1"
    assert git(repo, "tag") == "v0.1.1"
    assert git(repo, "rev-parse", "v0.1.1^{commit}") == git(repo, "rev-parse", "HEAD")
    assert git(repo, "cat-file", "-t", "v0.1.1") == "tag"  # annotated, like hand-made tags
    assert git(repo, "ls-remote", "--tags", "origin", "v0.1.1")


def test_release_with_nothing_new_tags_the_current_version(repo):
    assert run_ship(repo, "--release").returncode == 0
    assert versions(repo) == "0.1.0"  # no bump
    assert git(repo, "tag") == "v0.1.0"
    res = run_ship(repo, "--release")  # already tagged
    assert res.returncode == 1 and "tag v0.1.0 already exists" in res.stderr


def test_release_needs_a_push(repo):
    work_commit(repo)
    res = run_ship(repo, "--release", "--no-push")
    assert res.returncode == 1 and "--release needs a push" in res.stderr
    assert versions(repo) == "0.1.0"


def test_release_dry_run(repo):
    work_commit(repo)
    res = run_ship(repo, "--release", "--dry-run")
    assert res.returncode == 0
    assert "Would bump 0.1.0 -> 0.1.1, then push main, then tag v0.1.1" in res.stdout
    assert git(repo, "tag") == ""


def test_major_and_exact_versions(repo):
    assert run_ship(repo, "major").returncode == 0  # explicit: bumps even with nothing else new
    assert versions(repo) == "1.0.0"
    assert run_ship(repo, "minor", "--no-push").returncode == 0
    assert versions(repo) == "1.1.0"
    assert git(repo, "rev-list", "--count", "origin/main..HEAD") == "1"  # committed, not pushed
    assert run_ship(repo, "3.0.0").returncode == 0
    assert versions(repo) == "3.0.0"


def test_nothing_to_push_means_no_bump(repo):
    res = run_ship(repo)
    assert res.returncode == 0 and "Nothing to push" in res.stdout
    assert versions(repo) == "0.1.0"


def test_uncommitted_changes_refused(repo):
    work_commit(repo)
    (repo / "feature.txt").write_text("edited, not committed\n")
    res = run_ship(repo)
    assert res.returncode == 1 and "uncommitted changes" in res.stderr
    assert versions(repo) == "0.1.0"


def test_failed_push_is_retried_without_a_second_bump(repo, tmp_path):
    work_commit(repo)
    git(repo, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    res = run_ship(repo)
    assert res.returncode == 1 and "push" in res.stderr
    assert versions(repo) == "0.1.1"  # bumped and committed locally
    git(repo, "remote", "set-url", "origin", str(tmp_path / "origin.git"))
    res = run_ship(repo)
    assert res.returncode == 0, res.stderr
    assert versions(repo) == "0.1.1"  # reused, not 0.1.2
    assert git(repo, "rev-parse", "HEAD") == git(repo, "rev-parse", "origin/main")


def test_static_pyproject_version_refused(repo):
    # __init__.py is the single version source; a second one in pyproject.toml would drift
    work_commit(repo)
    toml = repo / "pyproject.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8").replace('dynamic = ["version"]', 'version = "0.1.0"')
    )
    git(repo, "commit", "-qam", "static version")
    res = run_ship(repo)
    assert res.returncode == 1 and "pyproject.toml sets version" in res.stderr
    assert versions(repo) == "0.1.0"


def test_dry_run_changes_nothing(repo):
    work_commit(repo)
    res = run_ship(repo, "--dry-run")
    assert res.returncode == 0 and "Would bump 0.1.0 -> 0.1.1, then push main" in res.stdout
    assert versions(repo) == "0.1.0"
    assert git(repo, "rev-list", "--count", "origin/main..HEAD") == "1"


def test_this_repo_versions_agree():
    assert ship.read_version(ROOT)  # __init__.py is the only version source
