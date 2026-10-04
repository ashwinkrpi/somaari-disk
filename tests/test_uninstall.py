"""packaging/linux/uninstall.sh against a fake HOME and stubbed dpkg/apt/pgrep (no root, no real
package, never touches real processes or folders)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packaging" / "linux" / "uninstall.sh"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None or os.geteuid() == 0,
    reason="Linux shell script; run as a normal user",
)

STUBS = {
    # status comes from FAKE_STATUS: "" = unknown package, "ii " = installed
    "dpkg-query": 'printf "%s" "${FAKE_STATUS:-}"; [ -n "${FAKE_STATUS:-}" ]',
    "dpkg": "exit 0",  # every system path counts as package-owned: never "left over"
    "apt-get": 'echo "apt-get $*" >> "$FAKE_CALLS"',
    "pgrep": "exit 1",  # no running app
    "update-desktop-database": "exit 0",
}


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        stub = bin_dir / name
        stub.write_text(f"#!/bin/sh\n{body}\n")
        stub.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    return {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(home),
        "FAKE_CALLS": str(tmp_path / "calls.txt"),
        "LANG": "C",
    }


def app_data(home: Path) -> list[Path]:
    paths = [
        home / ".config" / "somaari-disk" / "logs",
        home / ".cache" / "somaari-disk" / "scans",
        home / ".config" / "disk-usage-analyzer",
        home / ".cache" / "disk-usage-analyzer",
    ]
    for p in paths:
        p.mkdir(parents=True)
        (p / "f").write_text("x")
    menu = home / ".local" / "share" / "applications"
    menu.mkdir(parents=True)
    (menu / "somaari-disk.desktop").write_text("[Desktop Entry]\n")
    (menu / "other-app.desktop").write_text("[Desktop Entry]\n")
    return [*paths, menu / "somaari-disk.desktop"]


def run(env, *args, status=""):
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env={**env, "FAKE_STATUS": status},
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_script_syntax_and_help():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
    out = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
    assert out.returncode == 0 and "--keep-data" in out.stdout and "--dry-run" in out.stdout


def test_unknown_option_is_rejected(env):
    res = run(env, "--force")
    assert res.returncode == 2 and "unknown option" in res.stderr


def test_removes_app_data_and_is_repeatable(env):
    home = Path(env["HOME"])
    paths = app_data(home)
    res = run(env)
    assert res.returncode == 0, res.stderr
    assert not any(p.exists() for p in paths)
    assert (home / ".local/share/applications/other-app.desktop").exists()  # others kept
    assert "done" in res.stdout

    again = run(env)
    assert again.returncode == 0
    assert "nothing to remove" in again.stdout and "removed:" not in again.stdout


def test_keep_data_keeps_settings_and_scans(env):
    home = Path(env["HOME"])
    paths = app_data(home)
    res = run(env, "--keep-data")
    assert res.returncode == 0
    assert all(p.exists() for p in paths[:4])
    assert not paths[4].exists()  # the hand-made menu entry still goes


def test_dry_run_removes_nothing(env):
    home = Path(env["HOME"])
    paths = app_data(home)
    res = run(env, "--dry-run", status="ii ")
    assert res.returncode == 0
    assert all(p.exists() for p in paths)
    assert "would run: apt-get purge" in res.stdout
    assert f"would remove: {home}/.config/somaari-disk" in res.stdout
    assert not Path(env["FAKE_CALLS"]).exists()


def test_installed_package_without_root_asks_for_sudo(env):
    home = Path(env["HOME"])
    paths = app_data(home)
    res = run(env, status="ii ")
    assert res.returncode == 1
    assert "run this script with sudo" in res.stdout and "run again with sudo" in res.stdout
    assert not Path(env["FAKE_CALLS"]).exists()  # never tries apt without root
    assert not any(p.exists() for p in paths)  # own data is still cleaned


def test_uninstall_doc_points_at_script():
    doc = (ROOT / "UNINSTALL.md").read_text(encoding="utf-8")
    assert "packaging/linux/uninstall.sh" in doc and "## Windows" in doc
    for option in ("--keep-data", "--dry-run"):
        assert option in doc
