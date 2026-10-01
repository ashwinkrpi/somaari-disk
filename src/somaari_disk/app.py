"""Desktop window: pywebview + the api bridge. Thin; all logic lives in api/ and core/."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .api.bridge import Api

UI_DIR = Path(__file__).parent / "ui"
APP_NAME = "somaari-disk"
WEBVIEW2_MISSING = (
    "Disk Usage needs the Microsoft Edge WebView2 Runtime, which is not installed.\n\n"
    "Install the Evergreen WebView2 Runtime from Microsoft, then start Disk Usage again."
)


def gui_backend(platform: str = sys.platform) -> str:
    """pywebview backend: WebView2 (Edge Chromium) on Windows, GTK + WebKitGTK elsewhere."""
    return "edgechromium" if platform == "win32" else "gtk"


def make_api(demo: bool = False) -> Api:
    from platformdirs import user_cache_path, user_config_path

    from .logsetup import setup_logging

    config = user_config_path(APP_NAME)
    setup_logging(config / "logs", Path.home())
    prefs = config / "prefs.json"
    if demo:
        from .platform.demo import demo_platform

        return Api(demo_platform(), prefs_path=prefs)  # demo scans are never cached
    from .platform import get_platform

    cache = user_cache_path(APP_NAME) / "scans"
    return Api(get_platform(), home=Path.home(), prefs_path=prefs, cache_dir=cache)


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


def run(demo: bool = False, debug: bool = False) -> int:
    if sys.platform == "win32":
        from .platform.windows import message_box, webview2_version

        if not webview2_version():
            message_box("Disk Usage", WEBVIEW2_MISSING)
            return 1
    import webview  # lazy: heavy, GUI-only

    api = make_api(demo)
    window = webview.create_window(
        "Disk Usage" + (" (demo)" if demo else ""),
        str(UI_DIR / "index.html"),
        js_api=api,
        width=1280,
        height=800,
        min_size=(1024, 600),
        background_color="#16202C",
    )
    assert window is not None
    api.set_emitter(lambda event, payload: window.evaluate_js(js_event_call(event, payload)))
    wire_window_events(window.events, api)
    webview.start(gui=gui_backend(), debug=debug)
    return 0
