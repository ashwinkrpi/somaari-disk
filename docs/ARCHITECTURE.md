# Architecture
Layers (arrows = allowed imports): ui -> api -> core -> platform(interface). linux/windows/fake implement the interface.
- core/: scanner, tree, aggregation, models. Pure Python.
- platform/: `Platform` ABC: list_volumes, size_on_disk, owner_and_perms, is_hidden, trash, reveal, watch_volumes. Chosen at startup by sys.platform; tests use fake.
- api/: thin pywebview bridge. Plain dicts only. Long work runs in threads, results pushed as events.
- ui/: index.html, app.css (tokens as CSS vars), app.js (vanilla ES modules), fonts/. State = one object, render functions per panel.

Models (core/models.py, pydantic or dataclass): Node(name, size, size_on_disk, is_dir, mtime, ctime, child_count), Volume, ScanWarning, ScanProgress.
Tree is stored compactly (arrays/slots, interned names); only top-N children per folder are sent to the UI.
Events: scan_progress, scan_done, volumes_changed, error.
