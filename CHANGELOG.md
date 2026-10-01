# Changelog

- [2026-10-01] Stage 0: src layout, pyproject, CI, smoke tests, pywebview hello window, pre-commit - foundation + Pi window spike
- [2026-10-01] Allow PSF-2.0 in THIRD-PARTY-NOTICES - typing_extensions (pywebview dep) uses it
- [2026-10-01] Stage 1: core scanner (thread pool, cancel, progress, warnings, hard links once, single filesystem), tree roll-up, `scan <path>` CLI, minimal platform interface + local/fake platforms - scanning engine before UI
- [2026-10-01] Stage 2: Platform ABC extended (list_volumes, owner_and_perms, is_hidden, trash, reveal), linux.py (psutil + lsblk volumes), windows.py stubs, fake volumes/trash, VolumeWatcher hot-plug poll, volume-lost-mid-scan handling, `volumes` CLI, psutil/send2trash deps - storage devices before the API
- [2026-10-01] cli.print_report resolves stdout per call - default bound at import time ignored redirected/captured stdout
