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

## Deferred
