"""Release checks: dependency licenses, version consistency, packaging layout (no real installs)."""

import configparser
import importlib.util
import re
import stat
import struct
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

import pytest

import somaari_disk
from somaari_disk import app

ROOT = Path(__file__).resolve().parents[1]
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
PERMISSIVE = ("MIT", "BSD", "APACHE", "PSF", "PYTHON SOFTWARE FOUNDATION", "ISC")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _runtime_dists():
    """Installed runtime dependency tree for this OS (markers evaluated; extras skipped)."""
    from packaging.requirements import Requirement

    seen, todo, found = set(), [Requirement(r) for r in PROJECT["dependencies"]], []
    while todo:
        req = todo.pop()
        if req.marker and not req.marker.evaluate({"extra": ""}):
            continue
        if _norm(req.name) in seen:
            continue
        seen.add(_norm(req.name))
        try:
            dist = distribution(req.name)
        except PackageNotFoundError:
            continue  # CI core job installs with --no-deps; the release job installs everything
        found.append(dist)
        todo += [Requirement(r) for r in dist.requires or []]
    return found


def _license_text(dist) -> str:
    meta = dist.metadata
    parts = [meta.get("License-Expression") or "", (meta.get("License") or "")[:200]]
    parts += [c for c in meta.get_all("Classifier") or [] if c.startswith("License")]
    text = " ".join(parts).strip() or _license_file_text(dist)
    if "Permission is hereby granted, free of charge" in text:  # MIT license body
        text += " MIT"
    return text.upper()


def _license_file_text(dist) -> str:
    """Start of the bundled LICENSE file, for packages (clr_loader) with no license field."""
    for f in dist.files or []:
        if f.name.upper().startswith(("LICENSE", "LICENCE", "COPYING")):
            path = Path(dist.locate_file(f))
            return path.read_text(encoding="utf-8", errors="replace")[:600]
    return ""


def test_license_file_fallback_reads_real_package():
    text = _license_file_text(distribution("pytest"))  # pytest ships LICENSE (MIT)
    assert "Permission is hereby granted, free of charge" in text


def test_runtime_dependencies_are_permissive_and_listed():
    pytest.importorskip("packaging")
    notices = _norm((ROOT / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8"))
    dists = _runtime_dists()
    for dist in dists:
        name = dist.metadata["Name"]
        text = _license_text(dist)
        assert any(word in text for word in PERMISSIVE), f"{name}: license not permissive: {text}"
        assert "GPL" not in text.replace("LGPL", ""), f"{name}: GPL license: {text}"
        assert _norm(name) in notices, f"{name} missing from THIRD-PARTY-NOTICES.md"


def test_versions_agree():
    # __init__.py is the single version source; pyproject.toml reads it via hatch
    assert "version" not in PROJECT and PROJECT["dynamic"] == ["version"]
    assert somaari_disk.__version__
    assert PROJECT["license"] == "MIT"
    assert (ROOT / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")


def test_public_repo_files_exist():
    for name in (
        "README.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "LICENSE",
        "docs/GUIDE.md",
        "UNINSTALL.md",
    ):
        assert (ROOT / name).is_file(), name
    templates = ROOT / ".github" / "ISSUE_TEMPLATE"
    assert {p.name for p in templates.iterdir()} >= {"bug_report.yml", "feature_request.yml"}
    assert (ROOT / ".github" / "workflows" / "release.yml").is_file()


def test_readme_and_guide_images_exist():
    # Markdown ![..](path) and HTML <img src="path">; guide paths are relative to docs/.
    for doc, base in (("README.md", ROOT), ("docs/GUIDE.md", ROOT / "docs")):
        text = (ROOT / doc).read_text(encoding="utf-8")
        images = re.findall(r'(?:\]\(|src=")((?:docs/)?(?:screenshots|images)/[^)"]+)', text)
        assert images, doc
        for image in images:
            assert (base / image).is_file(), f"{doc}: {image}"


def test_window_icon_on_linux_only():
    assert app.window_icon("linux") == str(app.UI_DIR / "icon.png")
    assert Path(app.window_icon("linux")).is_file()
    assert app.window_icon("win32") is None


def test_ico_is_valid_png_icon():
    data = (ROOT / "packaging" / "icons" / "somaari-disk.ico").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data)
    assert (reserved, kind) == (0, 1) and count >= 4
    sizes = set()
    for i in range(count):
        w, _h, _c, _r, _p, _bpp, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        assert data[offset : offset + 8] == b"\x89PNG\r\n\x1a\n"
        assert offset + length <= len(data)
        sizes.add(w or 256)
    assert {16, 32, 48, 256} <= sizes


def _load_build_deb():
    spec = importlib.util.spec_from_file_location(
        "build_deb", ROOT / "packaging" / "linux" / "build_deb.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_install(target: Path) -> None:
    (target / "somaari_disk").mkdir()
    (target / "somaari_disk" / "__init__.py").write_text("")
    (target / "webview" / "lib").mkdir(parents=True)
    (target / "webview" / "lib" / "WebView2Loader.dll").write_bytes(b"x")
    (target / "webview" / "__pycache__").mkdir()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_deb_staging_layout(tmp_path):
    build_deb = _load_build_deb()
    staging = tmp_path / "pkg"
    build_deb.stage(staging, build_deb.project(), _fake_install)

    lib = staging / "usr" / "lib" / "somaari-disk"
    assert (lib / "somaari_disk" / "__init__.py").is_file()
    assert not (lib / "webview" / "lib").exists()  # Windows DLLs pruned
    assert not list(lib.rglob("__pycache__"))

    launcher = staging / "usr" / "bin" / "somaari-disk"
    assert launcher.stat().st_mode & stat.S_IXUSR
    assert "/usr/lib/somaari-disk" in launcher.read_text()
    assert (staging / "usr/share/icons/hicolor/scalable/apps/somaari-disk.svg").is_file()
    assert (staging / "usr/share/icons/hicolor/256x256/apps/somaari-disk.png").is_file()
    assert "MIT License" in (staging / "usr/share/doc/somaari-disk/copyright").read_text()

    desktop = configparser.ConfigParser(interpolation=None)
    desktop.optionxform = str
    desktop.read(staging / "usr/share/applications/somaari-disk.desktop")
    entry = desktop["Desktop Entry"]
    assert entry["Exec"] == "somaari-disk" and entry["Icon"] == "somaari-disk"
    assert entry["Terminal"] == "false"

    control = dict(
        line.split(": ", 1)
        for line in (staging / "DEBIAN" / "control").read_text().splitlines()
        if ": " in line and not line.startswith(" ")
    )
    assert control["Package"] == "somaari-disk"
    assert control["Version"] == somaari_disk.__version__
    assert control["Architecture"] == "all"
    assert "python3 (>= 3.11)" in control["Depends"]
    assert "gir1.2-webkit2-4.1" in control["Depends"]
    assert int(control["Installed-Size"]) > 0
    for script in ("postinst", "prerm"):
        assert (staging / "DEBIAN" / script).stat().st_mode & stat.S_IXUSR


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_deb_rejects_compiled_vendored_files(tmp_path):
    build_deb = _load_build_deb()

    def install(target: Path) -> None:
        (target / "psutil").mkdir()
        (target / "psutil" / "_psutil_linux.so").write_bytes(b"x")

    with pytest.raises(SystemExit, match="compiled"):
        build_deb.stage(tmp_path / "pkg", PROJECT, install)


def test_windows_packaging_files():
    win = ROOT / "packaging" / "windows"
    spec = (win / "somaari-disk.spec").read_text(encoding="utf-8")
    assert 'collect_data_files("somaari_disk")' in spec and "console=False" in spec
    iss = (win / "installer.iss").read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in iss
    assert re.search(r"AppId=\{\{[0-9A-F-]{36}\}", iss)
    assert "{#AppVersion}" in iss


def test_one_display_name_everywhere():
    title = app.APP_TITLE
    html = (app.UI_DIR / "index.html").read_text(encoding="utf-8")
    assert f"<title>{title}</title>" in html
    assert f'<h1 class="app-title">{title}</h1>' in html
    assert title in app.WEBVIEW2_MISSING
    assert f"Name={title}\n" in _load_build_deb().DESKTOP
    assert f"AppName={title}\n" in (ROOT / "packaging/windows/installer.iss").read_text("utf-8")
