# Decisions
Format: `- [date] decision - reason`
- [2026-10-01] Python core + pywebview + vanilla HTML/CSS/JS UI - reuses the HTML design, light on Pi, same code on Windows (WebView2).
- [2026-10-01] Only write operation is Move to Trash - safe for public release.
- [2026-10-01] Linux window backend = pywebview 6.2.1 GTK + WebKitGTK 4.1 - spike passed on Pi 5 (Debian 12 arm64, X11): window opens, page renders, ~160 MB RSS. Chromium --app fallback not needed.
- [2026-10-01] Linux setup: apt `python3-gi` `python3-venv` `gir1.2-webkit2-4.1` (pulls `libwebkit2gtk-4.1-0`); venv must use `--system-site-packages` so PyGObject comes from apt, not pip (no build toolchain needed).
- [2026-10-01] Build backend hatchling, src/ layout - standard, no setup.py.
- [2026-10-01] psutil/send2trash not declared until the stage that uses them - keep Stage 0 deps minimal.
- [2026-10-01] CI installs the package with --no-deps (no pywebview) - tests are core-only and never open windows; avoids GUI deps on runners.
- [2026-10-01] Git commits use the GitHub noreply address via repo-local config - no personal email in history.
- [2026-10-01] PSF-2.0 added to allowed licenses - permissive; needed for typing_extensions (pywebview dep).
- [2026-10-01] Stage 1 adds a minimal Platform ABC (stat, scandir) + LocalPlatform (os.scandir, Linux and Windows) + FakePlatform - core may not import os; Stage 2 extends the ABC and splits linux/windows.
- [2026-10-01] Scanner workers only list directories; the scan thread builds the tree - no locks on tree or hard-link set.
- [2026-10-01] Folder apparent size = sum of contents; size on disk also counts the folder's own blocks (like du). Size on disk = st_blocks*512, falling back to apparent size where st_blocks is missing (Windows, until Stage 7).
- [2026-10-01] Repeat hard links stay in the tree with size 0 (counted once per (dev, inode)). Symlinks and reparse-point dirs are skipped and counted, not shown.
- [2026-10-01] Dirs on another filesystem appear empty with an other_filesystem warning unless cross_mounts=True.

## Deferred
- Windows hard-link ids and true size on disk (scandir stat lacks st_ino/st_nlink/st_blocks) - Stage 7.
- Memory benchmark of the scan tree on a large real volume - Stage 6.
