# Testing

Automated: `.venv/bin/pytest -q` (fake platform + tmp_path only) and `.venv/bin/ruff check .`.
Below is the manual checklist for real hardware. Run from the Pi desktop, or over SSH with
`DISPLAY=:0`. Never put real file names or paths in issues, screenshots or notes; describe them
("a folder with an emoji name").

## Daily (about 10 minutes)

1. `.venv/bin/pytest -q`: all pass.
2. Start the app: `.venv/bin/python -m somaari_disk`.
   - The first start on a location scans it. Later starts open the saved scan right away,
     with the notice "Showing the last scan, from <date>".
   - Rescan replaces it, and the notice goes away.
3. Internal disk: choose the system drive in Location and let the scan finish. Then:
   - the usage bar matches `df -h /`,
   - drill in 3 levels and back with Up / Backspace,
   - Stop mid-scan shows "Scan stopped early".
4. USB drive (exFAT or NTFS, HDD or SSD): plug it in while the window is open. Then:
   - it appears in Location within a few seconds,
   - scan it,
   - Move one test file to the Trash: totals drop without a rescan, and the file is in the
     drive's Trash.
5. SD card (FAT32 or exFAT, through a reader): same as step 4. If the card has a lock switch,
   test the read-only case: Move to Trash is disabled with a plain reason.
6. Unplug mid-scan: start a scan of the USB drive and pull it out. Expect:
   - "Drive disconnected" right away, no freeze, no crash,
   - after replugging, the drive is back in Location under the same entry, and opening it
     shows its saved scan (from before the pull).
7. Odd names: on a USB stick, make a folder holding:
   - a name with spaces,
   - an emoji name,
   - a 200-character name,
   - a non-UTF-8 name (`touch "$(printf 'bad-\xff')"` on ext4).

   Scan it. Every item is listed (the bad byte shows as a replacement character). Details
   opens for each, and Show in folder works.
8. Log: open `~/.config/somaari-disk/logs/app.log`.
   - It has "scan N start/done" lines, but no file names and no home path (home shows as `~`).
   - It stays under 512 KiB; old logs are `app.log.1` to `.3`.
9. Idle: hide (minimize) the window. `top -p $(pgrep -f somaari_disk)` shows about 0%
   CPU.

## Weekly / before a release

- `.venv/bin/python -m somaari_disk bench`: a generated 1M-file tree.
  - Pi 5 reference: scan about 9 s, peak about 530 MiB, saved scan about 14 MiB, load about 6 s.
  - Investigate a jump of more than 25%.
- Big real volume: scan the largest drive you have. Watch `ps -o rss= -p <pid>` and note
  files / time / RSS in docs/DECISIONS.md if they differ much from the bench.
- Saved scans: `~/.cache/somaari-disk/scans/` holds at most 8 files. Deleting the
  folder is safe (the app rescans).
- Corrupt a saved scan (`truncate -s 100 <file>`), then start the app. It rescans without an
  error, and the log says "cache unreadable".
- Dark and light theme, at 1280x800 and 1024x600.
- CLI: `.venv/bin/python -m somaari_disk volumes` lists the internal disk, USB and SD
  with the right file system, removable and read-only flags.

## Windows (before a release)

Run from a normal (not admin) PowerShell: `.venv\Scripts\python -m somaari_disk`.
Setup: `py -3.11 -m venv .venv` then `.venv\Scripts\pip install -e ".[dev]"`.

1. Start: the window opens (WebView2). On a machine without the runtime, a dialog explains
   what to install and the app exits.
2. `.venv\Scripts\python -m somaari_disk volumes`: C: is system; a USB stick, USB HDD
   (NTFS, exFAT) and SD card are removable with the right file system; a mapped network drive
   shows as network; an empty card reader or DVD drive is not listed. No error dialogs.
3. Replug a USB drive into another port: its saved scan still opens (id survives replug).
4. Scan C:. No crash on access-denied folders (System Volume Information, other users).
   Junctions (e.g. "Documents and Settings") and OneDrive placeholders are not followed or
   downloaded; online-only files show 0 size on disk.
5. Hard links: C:\Windows sizes look sane (WinSxS counted once). Compare total with
   Explorer's Properties for one folder: within a few percent.
6. Long paths: a folder nested past 260 characters scans and its files show details.
7. Pull a USB drive mid-scan: "drive disconnected" state, no crash.
8. File details: owner is a user or group name, Permissions shows attributes, read-only
   file shows "(read-only)". Show in folder opens Explorer with the file selected.
9. Move to Trash a scratch file: it lands in the Recycle Bin and restores from there.
10. Hidden and system files are marked hidden. Light and dark theme follow Windows settings.
11. Minimize: Task Manager shows about 0% CPU. Close: the process exits.
12. Log in `%LOCALAPPDATA%` (platformdirs config dir) has no user name or home path.

