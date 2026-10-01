"""Static checks on ui/: no network, tokens present, JS only calls real api methods."""

import re
import sys
from pathlib import Path

import somaari_disk
from somaari_disk.__main__ import main
from somaari_disk.api.bridge import Api

UI = Path(somaari_disk.__file__).parent / "ui"
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
    from somaari_disk.api.bridge import PROBLEMS

    for flag in set(PROBLEMS.values()):
        assert f"{flag}:" in JS.split("PROBLEM_TAGS = ", 1)[1].split("\n", 1)[0], flag


def _palette(block: str) -> dict[str, str]:
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})", block))


def _luminance(hex_: str) -> float:
    def channel(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# WCAG 2.1 AA: 4.5:1 for text, 3:1 for focus rings and other UI parts (1.4.11)
TEXT_PAIRS = [
    ("text", "card"), ("text", "page-bg"), ("text", "selected-bg"), ("text", "subtle"),
    ("text", "control-bg"), ("muted", "card"), ("muted", "subtle"), ("muted", "page-bg"),
    ("muted", "selected-bg"), ("muted", "detail-header"), ("destructive", "card"),
    ("destructive", "subtle"), ("on-destructive", "destructive"), ("ok-text", "card"),
    ("ok-text", "subtle"), ("warn-text", "card"), ("low-text", "card"), ("full-text", "card"),
    ("accent", "card"), ("top-text", "top-bg"), ("top-muted", "top-bg"),
    ("top-text", "top-control-bg"),
]  # fmt: skip
UI_PAIRS = [("ring", "card"), ("ring", "selected-bg"), ("top-ring", "top-bg")]


def test_contrast_wcag_aa_light_and_dark():
    root, dark_block = CSS.split(':root[data-theme="dark"]', 1)
    light = _palette(root)
    dark = {**light, **_palette(dark_block.split("}", 1)[0])}
    bad = []
    for name, pal in (("light", light), ("dark", dark)):
        for pairs, need in ((TEXT_PAIRS, 4.5), (UI_PAIRS, 3.0)):
            for fg, bg in pairs:
                ratio = contrast(pal[fg], pal[bg])
                if ratio < need:
                    bad.append(f"{name} {fg} on {bg}: {ratio:.2f}")
    assert not bad, bad


def test_dark_theme_overrides_every_color():
    root, dark_block = CSS.split(':root[data-theme="dark"]', 1)
    light, dark = _palette(root), _palette(dark_block.split("}", 1)[0])
    # top-panel tokens are dark in both themes by design; everything else needs a dark value
    shared = {k for k in light if k.startswith("top-")}
    assert set(light) - shared - set(dark) == set()


def test_file_details_and_trash_dialog():
    for label in (
        "File details",
        "Full path",
        "Size on disk",
        "Modified",
        "Created",
        "Owner",
        "Permissions",
        "Show in folder",
        "Move to Trash",
    ):
        assert label in JS, label
    # confirm dialog: name + size, Cancel first and focused, destructive button styled
    assert '<dialog id="confirm"' in HTML and 'id="confirm-name"' in HTML
    assert 'id="confirm-size"' in HTML
    assert HTML.index('id="confirm-cancel"') < HTML.index('id="confirm-ok"')
    assert '$("confirm-cancel").focus()' in JS and "showModal()" in JS
    assert 'call("move_to_trash", item.segments, true)' in JS
    assert ".action.danger" in CSS and ".dialog::backdrop" in CSS
    # read-only drive / scan running: button disabled with the reason next to it
    assert "trash_blocked" in JS and '"aria-describedby"' in JS
    assert 'role", "alert"' in JS


def test_ui_has_no_os_paths():
    assert "/home/" not in JS and "C:\\" not in JS


def test_main_rejects_unknown_args_without_gui(capsys):
    assert main(["--bogus"]) == 2
    assert "webview" not in sys.modules


def test_saved_scan_on_start_and_location_change_only():
    assert "startScan(defaultLocation(), true)" in JS
    assert "startScan(e.target.value, true)" in JS
    assert 'call("scan", location, cached)' in JS
    assert "else startScan(state.location);" in JS  # Rescan button: always a fresh scan
    assert "Showing the last scan, from ${formatDate(d.scanned_at)}" in JS
    assert "Opening the last scan..." in JS


def test_usage_scope_line_for_folder_scans():
    assert 'id="usage-scope"' in HTML and ".usage-scope" in CSS
    assert "u.whole_volume" in JS and "u.scanned_size" in JS
    assert "used on this drive" in JS
