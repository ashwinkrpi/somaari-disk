"""Desktop window: pywebview + the api bridge. Thin; all logic lives in api/ and core/."""

from __future__ import annotations

import json
from pathlib import Path

from .api.bridge import Api

UI_DIR = Path(__file__).parent / "ui"
APP_NAME = "disk-usage-analyzer"


def make_api(demo: bool = False) -> Api:
    from platformdirs import user_config_path

    prefs = user_config_path(APP_NAME) / "prefs.json"
    if demo:
        from .platform.demo import demo_platform

        return Api(demo_platform(), prefs_path=prefs)
    from .platform import get_platform

    return Api(get_platform(), home=Path.home(), prefs_path=prefs)


def js_event_call(event: str, payload: dict) -> str:
    """JS that delivers one event to the UI. json.dumps escapes everything unsafe in JS."""
    return f"window.duaEvent && window.duaEvent({json.dumps(event)}, {json.dumps(payload)})"


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
    window.events.shown += lambda: api.set_visible(True)
    window.events.restored += lambda: api.set_visible(True)
    window.events.minimized += lambda: api.set_visible(False)
    window.events.closed += api.shutdown
    webview.start(gui="gtk", debug=debug)
    return 0
