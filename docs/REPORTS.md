# Stage reports

## Stage 1: Core scanner (2026-10-01)
- Files: core/{models,scanner,tree}.py, platform/{base,local,fake}.py, platform/__init__.py, cli.py, __main__.py (`scan` subcommand), tests/test_scanner_{fake,local}.py, CHANGELOG, DECISIONS.
- Tests: 29 passed, 0 skipped (Pi). Lint: ruff check + format clean; pre-commit hooks passed.
- Needs me: `.venv/bin/python -m disk_usage_analyzer scan <big folder>` on the Pi (speed, RSS, Ctrl+C cancel); scan of a USB/SD drive and unplug mid-scan; confirm CI green on windows-latest after push (symlink/permission/hard-link tests skip there).
- Limitations: Windows size on disk = apparent size and no hard-link dedupe (Stage 7); repeat hard links show size 0; symlinks hidden, only counted.
- Deferred: Windows file ids/size on disk (Stage 7); memory benchmark on a large volume (Stage 6).
- CHANGELOG updated: yes.
