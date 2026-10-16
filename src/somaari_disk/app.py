"""Desktop window: pywebview + the api bridge. Thin; all logic lives in api/ and core/."""

from __future__ import annotations

import functools
import json
import logging
import os
import sys
import threading
from pathlib import Path

from .api.bridge import Api
from .core.window_state import (
    MIN_SIZE,
    Rect,
    WindowState,
    WindowTracker,
    parse,
    placement,
    serialize,
)

log = logging.getLogger(__name__)

UI_DIR = Path(__file__).parent / "ui"
APP_NAME = "somaari-disk"
APP_TITLE = "Somaari Disk"  # window, menu entry and installer all use this name
WEBVIEW2_MISSING = (
    "Somaari Disk needs the Microsoft Edge WebView2 Runtime, which is not installed.\n\n"
    "Install the Evergreen WebView2 Runtime from Microsoft, then start Somaari Disk again."
)


def gui_backend(platform: str = sys.platform) -> str:
    """pywebview backend: WebView2 (Edge Chromium) on Windows, GTK + WebKitGTK elsewhere."""
    return "edgechromium" if platform == "win32" else "gtk"


def window_icon(platform: str = sys.platform) -> str | None:
    """Window/taskbar icon for GTK. On Windows the frozen exe carries its own icon."""
    icon = UI_DIR / "icon.png"
    return str(icon) if platform != "win32" and icon.is_file() else None


def make_api(demo: bool = False) -> Api:
    from platformdirs import user_cache_path, user_config_path

    from .about import about_data
    from .logsetup import setup_logging

    config = user_config_path(APP_NAME)
    logs = config / "logs"
    setup_logging(logs, Path.home())
    prefs = config / "prefs.json"
    cache = user_cache_path(APP_NAME) / "scans"
    about = functools.partial(about_data, APP_NAME, data_dir=cache, config_dir=config, log_dir=logs)
    if demo:
        from .platform.demo import demo_platform

        # demo scans are never cached
        return Api(demo_platform(), prefs_path=prefs, about=about)
    from .platform import get_platform

    return Api(get_platform(), home=Path.home(), prefs_path=prefs, cache_dir=cache, about=about)


def window_state_path() -> Path:
    from platformdirs import user_config_path

    return user_config_path(APP_NAME) / "window.json"


def load_window_state(path: Path) -> WindowState | None:
    try:
        return parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None  # missing or unreadable: defaults


def save_window_state(path: Path, state: WindowState) -> None:
    """Atomic write; a failure only loses the remembered size."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(serialize(state), encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        log.warning("could not save window state: %s", type(e).__name__)


def reset_window_state(path: Path) -> None:
    path.unlink(missing_ok=True)


def position_supported(platform: str = sys.platform, env=os.environ) -> bool:
    """Wayland ignores app-requested window positions (XWayland via GDK_BACKEND=x11 is fine)."""
    if platform == "win32":
        return True
    return env.get("XDG_SESSION_TYPE") != "wayland" or env.get("GDK_BACKEND") == "x11"


def current_screens(webview) -> list[Rect]:
    try:
        return [Rect(s.x, s.y, s.width, s.height) for s in webview.screens]
    except Exception:  # noqa: BLE001 - no screen info: keep size, drop position
        return []


def wire_window_state(events, tracker: WindowTracker) -> None:
    """Track geometry from window events (no polling). pywebview fires each event on its
    own thread, so updates are serialized. Handlers return None as in wire_window_events."""
    lock = threading.Lock()

    def locked(fn):
        def handler(*args) -> None:
            with lock:
                fn(*args)

        return handler

    events.resized += locked(tracker.resized)
    events.moved += locked(tracker.moved)
    events.maximized += locked(tracker.on_maximized)
    events.minimized += locked(tracker.on_minimized)
    events.restored += locked(tracker.on_restored)


def js_event_call(event: str, payload: dict) -> str:
    """JS that delivers one event to the UI. json.dumps escapes everything unsafe in JS."""
    return f"window.duaEvent && window.duaEvent({json.dumps(event)}, {json.dumps(payload)})"


def wire_window_events(events, api: Api) -> None:
    """Hook window events to the api. pywebview puts handler return values in a set,
    so handlers must return None (api methods return dicts, which are unhashable)."""

    def on_visible() -> None:
        api.set_visible(True)

    def on_hidden() -> None:
        api.set_visible(False)

    def on_closed() -> None:
        api.shutdown()

    events.shown += on_visible
    events.restored += on_visible
    events.minimized += on_hidden
    events.closed += on_closed


def run(demo: bool = False, debug: bool = False, reset_window: bool = False) -> int:
    if sys.platform == "win32":
        from .platform.windows import message_box, webview2_version

        if not webview2_version():
            message_box(APP_TITLE, WEBVIEW2_MISSING)
            return 1
    import webview  # lazy: heavy, GUI-only

    api = make_api(demo)
    state_path = window_state_path()
    if reset_window:
        reset_window_state(state_path)
    start = placement(load_window_state(state_path), current_screens(webview), position_supported())
    window = webview.create_window(
        APP_TITLE + (" (demo)" if demo else ""),
        str(UI_DIR / "index.html"),
        js_api=api,
        width=start.width,
        height=start.height,
        x=start.x,
        y=start.y,
        maximized=start.maximized,
        min_size=MIN_SIZE,
        background_color="#16202C",
    )
    assert window is not None
    api.set_emitter(lambda event, payload: window.evaluate_js(js_event_call(event, payload)))
    wire_window_events(window.events, api)
    tracker = WindowTracker(start)
    wire_window_state(window.events, tracker)
    webview.start(gui=gui_backend(), debug=debug, icon=window_icon())
    save_window_state(state_path, tracker.state)  # once, after the window has closed
    return 0
