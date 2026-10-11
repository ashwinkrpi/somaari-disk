import json

import pytest

from somaari_disk import app
from somaari_disk.__main__ import main
from somaari_disk.core.window_state import (
    DEFAULT_SIZE,
    MIN_SIZE,
    Rect,
    WindowState,
    WindowTracker,
    parse,
    placement,
    serialize,
)

LEFT = Rect(0, 0, 1920, 1080)
RIGHT = Rect(1920, 0, 2560, 1440)
SMALL = Rect(0, 0, 1366, 768)


def test_parse_roundtrip():
    st = WindowState(1400, 900, 100, 50, True)
    assert parse(serialize(st)) == st
    assert parse(serialize(WindowState(1300, 820))) == WindowState(1300, 820)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "[]",
        '{"width": 1300}',
        '{"width": "1300", "height": 800}',
        '{"width": 0, "height": 800}',
        '{"width": true, "height": 800}',
        '{"width": 1300, "height": 800, "x": 5}',
        '{"width": 1300, "height": 800, "x": 5.5, "y": 1}',
    ],
)
def test_parse_rejects_corrupt(text):
    assert parse(text) is None


def test_parse_maximized_must_be_true():
    assert parse('{"width": 1300, "height": 800, "maximized": "yes"}').maximized is False


def test_placement_defaults_when_nothing_saved():
    assert placement(None, [LEFT]) == WindowState(*DEFAULT_SIZE)


def test_placement_keeps_valid_state_on_second_monitor():
    st = WindowState(1400, 900, 2100, 100)
    assert placement(st, [LEFT, RIGHT]) == st


def test_placement_monitor_removed_centres_on_remaining():
    st = WindowState(2400, 1300, 2100, 100, True)
    got = placement(st, [LEFT])
    assert (got.x, got.y) == (None, None)
    assert (got.width, got.height) == (1920, 1080)  # shrunk to fit the remaining screen
    assert got.maximized is True


def test_placement_resolution_shrunk():
    got = placement(WindowState(1800, 1000, 1500, 700), [SMALL])
    assert got == WindowState(1366, 768)


def test_placement_negative_coords_on_left_monitor_are_valid():
    left_of_primary = Rect(-1920, 0, 1920, 1080)
    st = WindowState(1280, 800, -1700, 100)
    assert placement(st, [LEFT, left_of_primary]) == st


def test_placement_title_bar_above_screen_is_rejected():
    got = placement(WindowState(1280, 800, 100, -200), [LEFT])
    assert (got.x, got.y) == (None, None)


def test_placement_barely_visible_is_rejected():
    got = placement(WindowState(1280, 800, 1900, 100), [LEFT])  # 20 px on screen
    assert got.x is None


def test_placement_enforces_min_size():
    got = placement(WindowState(300, 200, 10, 10), [LEFT])
    assert (got.width, got.height) == MIN_SIZE


def test_placement_without_position_support_or_screens():
    st = WindowState(1400, 900, 100, 50)
    assert placement(st, [LEFT], position_supported=False) == WindowState(1400, 900)
    assert placement(st, []) == WindowState(1400, 900)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_tracker_follows_resize_and_move():
    tr = WindowTracker(WindowState(1280, 800), Clock())
    tr.resized(1400, 900)
    tr.moved(30, 40)
    assert tr.state == WindowState(1400, 900, 30, 40)


def test_tracker_maximize_resize_before_event_is_undone():
    clock = Clock()
    tr = WindowTracker(WindowState(1280, 800, 10, 20), clock)
    clock.t += 5
    tr.resized(1920, 1050)  # caused by maximizing
    clock.t += 0.1
    tr.on_maximized()
    assert tr.state == WindowState(1280, 800, 10, 20, True)


def test_tracker_maximize_event_first_ignores_following_resize():
    clock = Clock()
    tr = WindowTracker(WindowState(1280, 800), clock)
    tr.resized(1300, 820)
    clock.t += 10  # user resize long ago: must be kept
    tr.on_maximized()
    tr.resized(1920, 1050)
    tr.moved(0, 0)
    assert tr.state == WindowState(1300, 820, None, None, True)


def test_tracker_unmaximize_and_minimize():
    tr = WindowTracker(WindowState(1280, 800, maximized=True), Clock())
    tr.on_minimized()
    tr.resized(1, 1)
    tr.on_restored()  # back from minimized: still maximized
    assert tr.maximized is True
    tr.on_restored()  # un-maximize
    assert tr.state == WindowState(1280, 800)


def test_save_load_reset(tmp_path):
    path = tmp_path / "cfg" / "window.json"
    assert app.load_window_state(path) is None
    st = WindowState(1400, 900, 5, 6, True)
    app.save_window_state(path, st)
    assert app.load_window_state(path) == st
    assert json.loads(path.read_text(encoding="utf-8"))["width"] == 1400
    app.reset_window_state(path)
    assert not path.exists()
    app.reset_window_state(path)  # no file: no error


def test_load_corrupt_file_gives_none(tmp_path):
    path = tmp_path / "window.json"
    path.write_bytes(b"\xff\xfe garbage")
    assert app.load_window_state(path) is None


def test_save_failure_does_not_raise(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    app.save_window_state(blocker / "window.json", WindowState(1280, 800))  # parent is a file


def test_position_supported():
    assert app.position_supported("win32", {"XDG_SESSION_TYPE": "wayland"})
    assert app.position_supported("linux", {"XDG_SESSION_TYPE": "x11"})
    assert app.position_supported("linux", {})
    assert not app.position_supported("linux", {"XDG_SESSION_TYPE": "wayland"})
    assert app.position_supported("linux", {"XDG_SESSION_TYPE": "wayland", "GDK_BACKEND": "x11"})


def test_current_screens_handles_failure():
    class Screen:
        x, y, width, height = 0, 0, 1920, 1080

    class Ok:
        screens = [Screen()]

    class Broken:
        @property
        def screens(self):
            raise RuntimeError("no display")

    assert app.current_screens(Ok()) == [LEFT]
    assert app.current_screens(Broken()) == []


class FakeEvent:
    """Calls handlers the way pywebview's Event.set does (args unless no parameters)."""

    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

    def fire(self, *args):
        import inspect

        out = set()
        for fn in self.handlers:
            out.add(fn() if not inspect.signature(fn).parameters else fn(*args))
        return out


class FakeEvents:
    def __init__(self):
        for name in ("resized", "moved", "maximized", "minimized", "restored"):
            setattr(self, name, FakeEvent())


def test_wire_window_state():
    events = FakeEvents()
    tr = WindowTracker(WindowState(1280, 800), Clock())
    app.wire_window_state(events, tr)
    assert events.resized.fire(1500, 900) == {None}
    assert events.moved.fire(7, 8) == {None}
    assert events.maximized.fire() == {None}
    assert events.minimized.fire() == {None}
    assert events.restored.fire() == {None}
    # resize and move in the same instant as "maximized" are its side effects: undone
    assert tr.state == WindowState(1280, 800, None, None, True)


@pytest.mark.parametrize(
    "args, expected",
    [
        (["--reset-window"], (False, False, True)),
        (["--demo", "--reset-window"], (True, False, True)),
        (["--debug"], (False, True, False)),
    ],
)
def test_main_passes_reset_window(monkeypatch, args, expected):
    seen = []
    monkeypatch.setattr(
        app, "run", lambda demo, debug, reset_window: seen.append((demo, debug, reset_window)) or 0
    )
    assert main(args) == 0
    assert seen == [expected]
