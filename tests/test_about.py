"""About section: about_data wiring, build stamp, api.get_about."""

import importlib.util
import json
import sys
import types
from pathlib import Path

from somaari_disk import __version__, about
from somaari_disk.api.bridge import Api
from somaari_disk.platform.fake import FakeDir, FakePlatform

ROOT = Path(__file__).resolve().parents[1]


def _load_stamp_build():
    spec = importlib.util.spec_from_file_location(
        "stamp_build", ROOT / "packaging" / "stamp_build.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_somaari_about(monkeypatch, calls):
    mod = types.ModuleType("somaari_about")

    def collect(app_name, app_version, **kw):
        calls.append((app_name, app_version, kw))
        return {"app_name": app_name, "app_version": app_version, "build_commit": "dev"}

    mod.collect = collect
    mod.as_text = lambda info, dev: f"{info['app_name']} {info['app_version']} / {dev['name']}"
    mod.load_developer = lambda: {"name": "Dev"}
    mod.load_debug_fields = lambda: [
        {"key": "app_name", "label": "App", "extra": 1},
        {"key": "app_version", "label": "Version"},
    ]
    monkeypatch.setitem(sys.modules, "somaari_about", mod)


def test_build_info_falls_back_without_stamp(monkeypatch):
    monkeypatch.setitem(sys.modules, "somaari_disk._build_info", None)  # import -> ImportError
    assert about.build_info() == (None, None)


def test_build_info_reads_stamp(monkeypatch):
    stamped = types.ModuleType("somaari_disk._build_info")
    stamped.BUILD_COMMIT, stamped.BUILD_DATE = "abc123def456", "2026-01-02"
    monkeypatch.setitem(sys.modules, "somaari_disk._build_info", stamped)
    assert about.build_info() == ("abc123def456", "2026-01-02")


def test_about_data_shape(monkeypatch, tmp_path):
    calls = []
    _fake_somaari_about(monkeypatch, calls)
    monkeypatch.setattr(about, "build_info", lambda: ("c0ffee", "2026-01-02"))
    data = about.about_data("demo", data_dir=tmp_path / "d", config_dir=None, log_dir=None)

    ((name, version, kw),) = calls
    assert (name, version) == ("demo", __version__)
    assert kw["build_commit"] == "c0ffee" and kw["build_date"] == "2026-01-02"
    assert kw["packages"] == about.KEY_PACKAGES and kw["data_dir"] == tmp_path / "d"
    assert kw["ai_provider"] is None and kw["ai_model"] is None
    assert data["fields"] == [
        {"key": "app_name", "label": "App"},
        {"key": "app_version", "label": "Version"},
    ]
    assert data["developer"] == {"name": "Dev"}
    assert data["text"] == f"demo {__version__} / Dev"
    json.dumps(data)


def test_stamp_writes_build_info(tmp_path):
    stamp_build = _load_stamp_build()
    target = stamp_build.stamp(tmp_path, commit="0123456789abcdef", date="2026-01-02")
    ns = {}
    exec(target.read_text(encoding="utf-8"), ns)  # noqa: S102 (our own generated file)
    assert ns["BUILD_COMMIT"] == "0123456789ab" and ns["BUILD_DATE"] == "2026-01-02"


def test_stamp_without_git(tmp_path, monkeypatch):
    stamp_build = _load_stamp_build()
    monkeypatch.setattr(stamp_build, "git_commit", lambda root=None: None)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "0")
    ns = {}
    exec(stamp_build.stamp(tmp_path).read_text(encoding="utf-8"), ns)  # noqa: S102
    assert ns["BUILD_COMMIT"] is None and ns["BUILD_DATE"] == "1970-01-01"


def test_stamp_build_info_is_git_ignored():
    assert "src/somaari_disk/_build_info.py" in (ROOT / ".gitignore").read_text(encoding="utf-8")


def test_api_get_about():
    api = Api(FakePlatform(FakeDir({})), lambda *a: None, about=lambda: {"text": "x"})
    assert api.get_about() == {"ok": True, "text": "x"}
    assert Api(FakePlatform(FakeDir({})), lambda *a: None).get_about()["ok"] is False


def test_api_get_about_bug_stays_inside():
    def broken():
        raise RuntimeError("boom")

    res = Api(FakePlatform(FakeDir({})), lambda *a: None, about=broken).get_about()
    assert res["ok"] is False
