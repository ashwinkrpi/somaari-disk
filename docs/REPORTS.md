# Stage reports

## Stage 1: Core scanner (2026-10-01)
- Files: core/{models,scanner,tree}.py, platform/{base,local,fake}.py, platform/__init__.py, cli.py, __main__.py (`scan` subcommand), tests/test_scanner_{fake,local}.py, CHANGELOG, DECISIONS.
- Tests: 29 passed, 0 skipped (Pi). Lint: ruff check + format clean; pre-commit hooks passed.
- Needs me: `.venv/bin/python -m disk_usage_analyzer scan <big folder>` on the Pi (speed, RSS, Ctrl+C cancel); scan of a USB/SD drive and unplug mid-scan; confirm CI green on windows-latest after push (symlink/permission/hard-link tests skip there).
- Limitations: Windows size on disk = apparent size and no hard-link dedupe (Stage 7); repeat hard links show size 0; symlinks hidden, only counted.
- Deferred: Windows file ids/size on disk (Stage 7); memory benchmark on a large volume (Stage 6).
- CHANGELOG updated: yes.

## Stage 2: Platform layer + storage devices (2026-10-01)
- Files: platform/{base,local,linux,windows,fake,__init__}.py, core/{volumes,scanner,models}.py, cli.py + __main__.py (`volumes`), tests/test_platform_{linux,fake}.py, test_scanner_local.py (uses get_platform), pyproject (psutil, send2trash), ARCHITECTURE, PLATFORM, DECISIONS, CHANGELOG.
- Tests: 65 passed, 0 skipped (Pi). Lint: ruff check + format clean.
- Needs me: plug a USB stick + SD reader, run `.venv/bin/python -m disk_usage_analyzer volumes` (label, removable, fs, read-only right?); `scan <usb mount>` and unplug mid-scan (expect "Drive disconnected", exit 2); trash/reveal on a real USB file (UI calls them in Stage 5); CI on windows-latest after push.
- Limitations: Windows list_volumes/owner_and_perms/reveal are stubs (Stage 7); reveal opens the folder without selecting the file; volume id = device path (changes across replug).
- Deferred: FileManager1 ShowItems reveal (Stage 5); UUID-based volume ids, LVM/LUKS matching (Stage 6).
- CHANGELOG updated: yes.

## Stage 3: API + app shell (2026-10-01)
- Files: api/bridge.py (new), app.py (new), platform/demo.py (new), __main__.py (`--demo`, `--debug`), core/tree.py (sorted_children, find_node, remove_node), ui/{index.html, app.css, app.js}, tests/test_api.py, tests/test_ui_static.py, ARCHITECTURE, DECISIONS, CHANGELOG.
- Tests: 109 passed, 0 skipped (Pi). Lint: ruff check + format clean. One new test expectation fixed before commit (Unicode name sort is code point, not locale).
- Needs me: run `.venv/bin/python -m disk_usage_analyzer` (and `--demo`) with DISPLAY=:0 (launch was not approved in the autonomous run, so the window, JS bridge and events are untested live); compare the top panel to the boards at 1280x800 and 1024x600; theme select (System/Light/Dark) + OS switch; plug/unplug USB while the window is visible (Location list updates, "Disconnected drive"); bundle IBM Plex woff2 + OFL text into ui/fonts/ (download needed approval).
- Limitations: body is a status card only (no list yet), so Up/breadcrumb only work once Stage 4 drills in; tree not readable during a scan; app auto-scans Home on start; theme select not in the boards.
- Deferred: children list, usage bar, drill-down, progressive fill (Stage 4); file view, Created date, trash confirm dialog (Stage 5).
- CHANGELOG updated: yes.
