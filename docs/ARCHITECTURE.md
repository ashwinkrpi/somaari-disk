# Architecture
Layers (arrows = allowed imports): ui -> api -> core -> platform(interface). linux/windows/fake implement the interface.
- core/: scanner, tree, aggregation, models. Pure Python.
- platform/: `Platform` ABC (base.py): stat, scandir (size on disk, hard-link ids), list_volumes, owner_and_perms, is_hidden, trash, reveal. local.py = shared os.scandir/send2trash base; linux.py, windows.py (stubs until Stage 7), fake.py. Chosen at startup by sys.platform; tests use fake.
- Hot-plug: core/volumes.py `VolumeWatcher` polls list_volumes every few seconds only while resumed (window visible).
- api/: thin pywebview bridge (bridge.py `Api`). Plain dicts only; errors are `{ok: false, error: {code, message}}`, never exceptions. Paths cross as segment lists relative to the scan root; breadcrumbs = root path parts (not navigable) + segments. Long work runs in threads, results pushed as events via `window.duaEvent(event, payload)`. Methods: list_volumes, scan(location), cancel_scan, get_children(segments, sort, limit, on_disk), get_details, get_usage, open_in_file_manager, move_to_trash(segments, confirmed=True), get_prefs/set_theme, set_visible.
- app.py: creates the window, wires window events (shown/minimized/restored -> set_visible, closed -> shutdown). `--demo` uses platform/demo.py (generated tree on fake drives).
- ui/: index.html, app.css (tokens as CSS vars), app.js (vanilla ES modules), fonts/. State = one object, render functions per panel. app.js resolves the theme (OS preference or override) to data-theme=light|dark.

Models (core/models.py, dataclasses): Node(name, size, size_on_disk, is_dir, mtime, ctime, child_count), ScanWarning, ScanProgress, ScanResult. Volume and Permissions live in platform/base.py (platform may not import core) and are re-exported from core/models.
Tree is stored compactly (arrays/slots, interned names); only top-N children per folder are sent to the UI.
Events: scan_progress, scan_done, volumes_changed, error.
