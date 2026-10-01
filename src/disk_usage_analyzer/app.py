"""Desktop window: pywebview + the api bridge. Thin; all logic lives in api/ and core/."""

from __future__ import annotations

import json
from pathlib import Path

from .api.bridge import Api

UI_DIR = Path(__file__).parent / "ui"
APP_NAME = "disk-usage-analyzer"


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
    webview.start(gui="gtk", debug=debug)
    return 0
