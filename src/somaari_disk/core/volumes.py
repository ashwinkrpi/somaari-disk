"""Hot-plug detection by polling list_all_volumes (mounted + unmounted).
Polls only while resumed (window visible)."""

from __future__ import annotations

import threading
from collections.abc import Callable

from ..platform.base import Platform, Volume

VolumesCallback = Callable[[list[Volume]], None]


class VolumeWatcher:
    """Calls `on_change(volumes)` when a volume appears, disappears or changes identity.
    Volumes include unmounted removable partitions (is_mounted=False), so a card that is
    inserted or mounted by another app shows up too.

    Starts paused. resume() when the window is shown, pause() when hidden: a paused
    watcher's thread blocks without waking, so idle CPU stays at zero.
    """

    def __init__(self, platform: Platform, on_change: VolumesCallback, interval: float = 3.0):
        self._platform = platform
        self._on_change = on_change
        self._interval = interval
        self._running = threading.Event()  # set = polling
        self._wake = threading.Event()  # interrupts the wait between polls
        self._stopped = False
        self._known: tuple | None = None
        self._thread: threading.Thread | None = None

    def snapshot(self) -> list[Volume]:
        """Current volumes; also becomes the baseline for change detection."""
        vols = self._list()
        self._known = self._key(vols)
        return vols

    def resume(self) -> None:
        if self._stopped:
            return
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="volumes")
            self._thread.start()
        self._running.set()
        self._wake.set()  # poll now: drives may have changed while hidden

    def pause(self) -> None:
        self._running.clear()

    def stop(self) -> None:
        self._stopped = True
        self._running.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def polling(self) -> bool:
        return self._running.is_set() and not self._stopped

    def _list(self) -> list[Volume]:
        try:
            return self._platform.list_all_volumes()
        except Exception:  # never let a platform hiccup kill the watcher
            return []

    @staticmethod
    def _key(vols: list[Volume]) -> tuple:
        return tuple(sorted(v.identity() for v in vols))

    def _loop(self) -> None:
        while True:
            self._running.wait()
            if self._stopped:
                return
            self._wake.clear()
            vols = self._list()
            key = self._key(vols)
            if key != self._known:
                changed = self._known is not None
                self._known = key
                if changed:
                    try:
                        self._on_change(vols)
                    except Exception:
                        pass  # a broken listener must not stop hot-plug detection
            self._wake.wait(self._interval)
            if self._stopped:
                return
