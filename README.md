# somaari-disk

See what is using space on your drives. Somaari Disk scans a drive or folder and shows the
largest items first, with a usage bar, folder totals and file details. It works on internal
disks and external drives (USB sticks, SD cards, NVMe, exFAT/NTFS/FAT32, read-only mounts).

![Main screen, light theme](docs/screenshots/main-light.png)
![Main screen, dark theme](docs/screenshots/main-dark.png)

*Screenshots use the built-in demo tree, not real files.*

## What it does and does not do

- Reads only. The one change it can make is **Move to Trash**, after you confirm. There is no
  permanent delete; restore from the system Trash or Recycle Bin.
- No network access, no telemetry, no accounts.
- Never follows symlinks or junctions, stays on one drive, counts hard links once.
- Scans run in the background, can be stopped, and skip folders it cannot read.
- Remembers the last scan of each location so it opens instantly; Rescan scans fresh.
- Light and dark themes (follows the OS, or pick one).

Runs on Linux (tested on Raspberry Pi 5, arm64) and Windows 10/11 (x64).

## Install

Download from [Releases](https://github.com/ashwinkrpi/somaari-disk/releases):

- **Debian, Ubuntu, Raspberry Pi OS** (arm64 and x86-64): `somaari-disk_<version>_all.deb`

  ```sh
  sudo apt install ./somaari-disk_<version>_all.deb
  ```

  Needs Python 3.11+ (Debian 12, Ubuntu 23.04 or newer). Then open **Somaari Disk** from the
  app menu, or run `somaari-disk`.
- **Windows 10/11 x64**: `somaari-disk-<version>-setup.exe` (installs for your user, no admin),
  or the portable `somaari-disk-<version>-windows-x64.zip`. Needs the Microsoft Edge WebView2
  Runtime, which Windows 11 includes; the app tells you if it is missing. Builds are not code
  signed yet, so SmartScreen may warn: choose **More info**, then **Run anyway**.
- **Windows 10**: Windows Update installs WebView2 on most PCs. Older or un-updated PCs and
  LTSC editions may not have it: if the app says it is missing, install the **Evergreen
  Bootstrapper** from Microsoft's WebView2 download page, then start the app again. The
  installer does not include WebView2.

Check downloads against `SHA256SUMS` from the same release.

Other Linux distributions, or running from source: see [INSTALL.md](INSTALL.md). To uninstall: [UNINSTALL.md](UNINSTALL.md).

## Use

```sh
somaari-disk            # open the window
somaari-disk --demo     # try it on a generated sample tree
somaari-disk scan PATH  # text report, no window
somaari-disk volumes    # list drives
```

Keyboard (everything works without a mouse):

- List: arrows, Home/End, PgUp/PgDn move; Enter opens a folder; Backspace or Alt+Up goes up.
- Select several: Shift+arrows (range), Ctrl+arrows move without selecting, then Ctrl+Space
  adds/removes a row; Space selects only the focused row; Ctrl+A all; Esc back to one.
- Delete moves the selected files to Trash (asks first). In the dialog, Left/Right switch
  between Cancel and Move to Trash; Tab reaches the list of names; Esc cancels.
- Tab moves between controls; F6 / Shift+F6 jump between the top bar, the list and the details
  panel; Left/Right move between the panel's buttons and the breadcrumb.
- F5 rescans; Esc stops a scan.

## Develop

```sh
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format .
scripts/ship.py          # push: bumps the patch version (0.1.0 -> 0.1.1), commits, pushes
scripts/ship.py major    # bigger bump: minor (0.2.0), major (1.0.0) or an exact X.Y.Z
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and
[docs/TESTING.md](docs/TESTING.md). Security issues: [SECURITY.md](SECURITY.md).

## License

MIT, see [LICENSE](LICENSE). Bundled fonts and dependencies: [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
