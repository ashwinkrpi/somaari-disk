"""Static checks on ui/: no network, tokens present, JS only calls real api methods."""

import re
import sys
from pathlib import Path

import disk_usage_analyzer
from disk_usage_analyzer.__main__ import main
from disk_usage_analyzer.api.bridge import Api

UI = Path(disk_usage_analyzer.__file__).parent / "ui"
HTML = (UI / "index.html").read_text(encoding="utf-8")
CSS = (UI / "app.css").read_text(encoding="utf-8")
JS = (UI / "app.js").read_text(encoding="utf-8")


def test_shell_files_linked():
    assert 'href="app.css"' in HTML
    assert 'type="module" src="app.js"' in HTML
    for el in ('id="location"', 'id="up"', 'id="rescan"', 'id="breadcrumb"', 'id="theme"'):
        assert el in HTML


def test_no_network_urls():
    for text in (HTML, CSS, JS):
        assert not re.search(r"(https?:)?//[a-z0-9.-]+\.[a-z]{2,}/", text, re.I)


def test_fonts_are_local():
    urls = re.findall(r'url\("([^"]+)"\)', CSS)
    assert urls and all(u.startswith("fonts/") for u in urls)
    assert "IBM Plex Sans" in CSS and "IBM Plex Mono" in CSS


def test_design_tokens_as_css_variables():
    light = ["#EEF0F3", "#16202C", "#4A5565", "#D5D9E0", "#E8F0FA", "#1F4E8C", "#B3261E"]
    dark = ["#0F141B", "#0A0E13", "#E6EAF0", "#1A222D", "#1E3A5F", "#4C8DDB", "#8CC1F0"]
    root, dark_block = CSS.split(':root[data-theme="dark"]', 1)
    for hex_ in light:
        assert hex_ in root, hex_
    for hex_ in dark:
        assert hex_ in dark_block, hex_


def test_theme_follows_os_with_override():
    assert "prefers-color-scheme: dark" in JS
    assert 'state.theme === "system"' in JS
    for value in ("system", "light", "dark"):
        assert f'value="{value}"' in HTML


def test_js_calls_only_existing_api_methods():
    called = set(re.findall(r'call\("(\w+)"', JS))
    assert called, "app.js should call the api"
    public = {n for n in dir(Api) if not n.startswith("_")}
    assert called <= public, called - public


def test_js_handles_every_event():
    for event in ("scan_progress", "scan_done", "volumes_changed", "error"):
        assert re.search(rf"\b{event}\(p\)", JS), event


def test_ui_has_no_os_paths():
    assert "/home/" not in JS and "C:\\" not in JS


def test_main_rejects_unknown_args_without_gui(capsys):
    assert main(["--bogus"]) == 2
    assert "webview" not in sys.modules
