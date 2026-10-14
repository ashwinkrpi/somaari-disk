# Uninstalling somaari-disk

- [Linux (.deb package)](#linux-deb-package)
- [Windows](#windows)

Running from source, or on Windows? See [Uninstalling](docs/GUIDE.md#uninstalling) in the guide.

## Linux (.deb package)

For Debian, Ubuntu and Raspberry Pi OS, when the app was installed from
`somaari-disk_<version>_all.deb`.

### What gets removed

| What | Where |
|---|---|
| The app | `/usr/lib/somaari-disk/` (app, bundled libraries, compiled bytecode) |
| Launcher | `/usr/bin/somaari-disk` |
| Menu entry and icon | `/usr/share/applications/somaari-disk.desktop`, `/usr/share/icons/hicolor/*/apps/somaari-disk.*` |
| License notes | `/usr/share/doc/somaari-disk/` |
| Settings and log | `~/.config/somaari-disk/` |
| Saved scans | `~/.cache/somaari-disk/` |
| Old test-build data | `~/.config/disk-usage-analyzer/`, `~/.cache/disk-usage-analyzer/` (the app's earlier name) |
| Hand-made menu entry | `~/.local/share/applications/somaari-disk.desktop` (only if you made one for a source checkout) |

Never removed: your files and drives (the app only reads them), files you moved to the Trash
(restore or empty them from the Trash as usual), a source checkout and its `.venv`, and the
system packages the app used (Python, GTK, WebKitGTK, psutil). Other apps share those.

### Clean uninstall with the script

The script is `packaging/linux/uninstall.sh` in this repository. You can run it as often as you
like: whatever is already gone is skipped, so a second run just says "nothing to remove".

1. Get the script, if you don't have the repository:

   ```sh
   curl -fLO https://raw.githubusercontent.com/ashwinkrpi/somaari-disk/main/packaging/linux/uninstall.sh
   ```

   (While the repository is private, copy the file from a checkout instead.)

2. Optional: see what it would remove, without removing anything:

   ```sh
   bash uninstall.sh --dry-run
   ```

3. Uninstall. It asks for your password because removing a package needs admin rights:

   ```sh
   sudo bash uninstall.sh
   ```

   From the repository folder, use the path instead: `sudo bash packaging/linux/uninstall.sh`.

Options:

| Option | Effect |
|---|---|
| `--keep-data` | Keep settings, log and saved scans (for example, before installing a newer version). |
| `--dry-run` | Only list what would be removed. No sudo needed. |
| `--help` | Show the options. |

What it does, in order:

1. Closes open Somaari Disk windows.
2. Removes the package with `apt-get purge` (also drops the package's record in apt).
3. Removes anything the package left behind, such as compiled bytecode or files from an
   interrupted install, then refreshes the menu and icon caches.
4. Removes your app data (skipped with `--keep-data`) and a hand-made menu entry.

Run with `sudo`, it removes the data of the user who ran `sudo`, not root's. Without `sudo` it
removes only your app data; if the package is still installed it says so and exits with an
error, so run it again with `sudo`.

### Uninstall by hand

```sh
sudo apt purge somaari-disk
rm -rf ~/.config/somaari-disk ~/.cache/somaari-disk
```

`apt remove` instead of `purge` also works; the app keeps no system-wide settings.

### Check that it is gone

```sh
dpkg -s somaari-disk                 # "package 'somaari-disk' is not installed"
ls /usr/lib/somaari-disk             # "No such file or directory"
command -v somaari-disk              # prints nothing
```

Somaari Disk should no longer be in the app menu. If the menu still shows it, log out and back
in (some desktops cache the menu).

### Reinstalling

Uninstalling first is not needed for an upgrade: `sudo apt install ./somaari-disk_<version>_all.deb`
replaces the old version. To start fresh, run `sudo bash uninstall.sh` first, then install.

## Windows

To be added.
