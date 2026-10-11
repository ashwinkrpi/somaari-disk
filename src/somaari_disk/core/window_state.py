"""Remembered window size/position: parse, validate against current screens, track changes.

Pure logic. The app reads/writes the JSON file and feeds pywebview events and screens in.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass

DEFAULT_SIZE = (1280, 800)
MIN_SIZE = (1024, 600)
VISIBLE_W, VISIBLE_H = 100, 50  # window area that must stay on a screen to be reachable
MAXIMIZE_RESIZE_WINDOW = 0.5  # s: a resize this close to "maximized" is the maximize itself


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class WindowState:
    width: int
    height: int
    x: int | None = None  # None = let the window manager place it (centred)
    y: int | None = None
    maximized: bool = False


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def parse(text: str) -> WindowState | None:
    """WindowState from saved JSON, or None if missing fields or wrong types."""
    try:
        d = json.loads(text)
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    w, h, x, y, m = (d.get(k) for k in ("width", "height", "x", "y", "maximized"))
    if not (_is_int(w) and _is_int(h) and w > 0 and h > 0):
        return None
    if not ((x is None and y is None) or (_is_int(x) and _is_int(y))):
        return None
    return WindowState(w, h, x, y, m is True)


def serialize(state: WindowState) -> str:
    return json.dumps(asdict(state))


def _overlap(a: Rect, b: Rect) -> tuple[int, int]:
    w = min(a.x + a.width, b.x + b.width) - max(a.x, b.x)
    h = min(a.y + a.height, b.y + b.height) - max(a.y, b.y)
    return max(w, 0), max(h, 0)


def _reachable(win: Rect, screen: Rect) -> bool:
    """Enough of the window on this screen, and its top edge (title bar) inside it."""
    w, h = _overlap(win, screen)
    top_ok = screen.y <= win.y <= screen.y + screen.height - VISIBLE_H
    return w >= VISIBLE_W and h >= VISIBLE_H and top_ok


def placement(
    saved: WindowState | None,
    screens: Sequence[Rect],
    position_supported: bool = True,
) -> WindowState:
    """Where to open the window. Falls back to default size, centred, when the saved
    state is missing or would land off-screen (monitor unplugged, resolution changed)."""
    if saved is None:
        return WindowState(*DEFAULT_SIZE)
    w, h = max(saved.width, MIN_SIZE[0]), max(saved.height, MIN_SIZE[1])
    x, y = saved.x, saved.y
    home = screens[0] if screens else None
    if x is not None and y is not None and position_supported and screens:
        win = Rect(x, y, w, h)
        home = next((s for s in screens if _reachable(win, s)), None)
    if home is None or not position_supported:
        x = y = None
        home = screens[0] if screens else None
    if home is not None:  # never larger than the screen it opens on
        w = max(min(w, home.width), MIN_SIZE[0])
        h = max(min(h, home.height), MIN_SIZE[1])
    return WindowState(w, h, x, y, saved.maximized)


class WindowTracker:
    """Follows resize/move/maximize events; `state` is what to save on close.

    Keeps the normal (un-maximized) geometry. Maximizing may resize/move the window just
    before the "maximized" event arrives, so changes within MAXIMIZE_RESIZE_WINDOW of it
    are undone.
    """

    def __init__(self, initial: WindowState, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._history: list[tuple[float, WindowState]] = [(float("-inf"), initial)]
        self.maximized = initial.maximized
        self.minimized = False

    @property
    def _normal(self) -> WindowState:
        return self._history[-1][1]

    def _set(self, st: WindowState) -> None:
        now = self._clock()
        cutoff = now - MAXIMIZE_RESIZE_WINDOW
        keep = [h for h in self._history if h[0] > cutoff]  # recent, maybe undone later
        older = [h for h in self._history if h[0] <= cutoff]
        self._history = older[-1:] + keep + [(now, st)]

    def _frozen(self) -> bool:
        return self.maximized or self.minimized

    def resized(self, width: int, height: int) -> None:
        if self._frozen() or width <= 0 or height <= 0:
            return
        n = self._normal
        self._set(WindowState(width, height, n.x, n.y))

    def moved(self, x: int, y: int) -> None:
        if self._frozen():
            return
        n = self._normal
        self._set(WindowState(n.width, n.height, x, y))

    def on_maximized(self) -> None:
        cutoff = self._clock() - MAXIMIZE_RESIZE_WINDOW
        self._history = [h for h in self._history if h[0] <= cutoff][-1:]
        self.maximized = True

    def on_minimized(self) -> None:
        self.minimized = True

    def on_restored(self) -> None:
        # Un-minimize keeps a maximized window maximized; un-maximize clears it.
        if self.minimized:
            self.minimized = False
        else:
            self.maximized = False

    @property
    def state(self) -> WindowState:
        n = self._normal
        return WindowState(n.width, n.height, n.x, n.y, self.maximized)
