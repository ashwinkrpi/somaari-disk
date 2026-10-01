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


def test_usage_bar_markup_and_states():
    assert 'role="progressbar"' in HTML and 'aria-valuenow="0"' in HTML
    assert "tick-50" in HTML and "tick-75" in HTML
    assert ".tick-50 { left: 50%; }" in CSS and ".tick-75 { left: 75%; }" in CSS
    # every api level has its own label and icon in JS and its colors in CSS
    for level, label in (
        ("ok", "Plenty of space"),
        ("filling", "Filling up"),
        ("low", "Running low"),
        ("full", "Disk full"),
    ):
        assert re.search(rf'{level}: \{{ label: "{label}", icon: "{level}" \}}', JS), level
        assert re.search(rf"^\s+{level}: \"M", JS, re.M), f"icon {level}"
    for level in ("filling", "low", "full", "gone"):
        assert f'.usage[data-level="{level}"]' in CSS
    dark = CSS.split(':root[data-theme="dark"]', 1)[1]
    for var in ("--ok-fill", "--warn-fill", "--low-fill", "--full-fill"):
        assert var in dark


def test_rows_are_toggle_buttons_with_keyboard_nav():
    assert 'el("button", "row")' in JS
    assert '"aria-pressed"' in JS
    assert 'addEventListener("dblclick"' in JS
    for key in ("ArrowDown", "ArrowUp", "Home", "End", "Enter", "Backspace"):
        assert key in JS, key
    assert 'id="rows"' in HTML and 'id="side"' in HTML


def test_list_states_covered():
    for text in (
        "Scanning...",
        "This folder is empty",
        "Couldn't scan this location",
        "No permission to open this folder",
        "Drive disconnected",
        "Other (",
    ):
        assert text in JS, text
    # every api problem flag has a row tag
    from disk_usage_analyzer.api.bridge import PROBLEMS

    for flag in set(PROBLEMS.values()):
        assert f"{flag}:" in JS.split("PROBLEM_TAGS = ", 1)[1].split("\n", 1)[0], flag


def test_ui_has_no_os_paths():
    assert "/home/" not in JS and "C:\\" not in JS


def test_main_rejects_unknown_args_without_gui(capsys):
    assert main(["--bogus"]) == 2
    assert "webview" not in sys.modules
