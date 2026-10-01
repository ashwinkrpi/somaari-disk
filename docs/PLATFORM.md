# Platform notes (Windows must implement before public)
- Volumes: drive letters via GetLogicalDrives; type via GetDriveType (fixed/removable/network/CD); label via GetVolumeInformation; usage via GetDiskFreeSpaceEx.
- Paths: long paths (\\?\ prefix), case-insensitive compare, no assumption of "/".
- Reparse points/junctions: detect via attributes; never follow.
- Size on disk: GetCompressedFileSize / cluster rounding; hard links via file index.
- Hidden/system: FILE_ATTRIBUTE_HIDDEN/SYSTEM.
- Owner/perms: simplified display (owner name + read-only flag).
- Trash: Recycle Bin via send2trash. Reveal: `explorer /select,<path>`.
- Packaging: WebView2 runtime check.

## Interface status (Stage 7)
| Method | Linux | Windows |
|---|---|---|
| stat / scandir | os.scandir, st_blocks, (dev, ino) | os.scandir + full lstat per file (file index, link count, volume serial); size on disk = size rounded to cluster, GetCompressedFileSizeW for compressed/sparse/cloud files; `\\?\` prefix past 248 chars |
| list_volumes | psutil mount table + `lsblk -J` (3 s timeout); network statvfs in a thread (2 s) | ctypes: GetLogicalDrives, GetDriveTypeW, GetVolumeInformationW, GetDiskFreeSpaceExW; id = volume GUID, else serial; USB/SD/1394 bus (IOCTL_STORAGE_QUERY_PROPERTY) = removable; non-fixed drives queried in a thread (2 s); no-media drives skipped |
| owner_and_perms | lstat + pwd/grp, stat.filemode, os.access(W_OK) | GetNamedSecurityInfoW + LookupAccountSidW (SID string fallback); group ""; mode = attribute names; read-only = attribute or read-only volume |
| is_hidden | dot name | FILE_ATTRIBUTE_HIDDEN or dot name (done) |
| trash | send2trash (shared): home trash or per-volume `.Trash-<uid>`; refuses volume roots and read-only folders | shared (Recycle Bin); manual check only |
| reveal | `xdg-open` on the folder (file: its parent; no selection) | `explorer /select,"<path>"` (file selected); folders open directly |
- Volume rules: skip pseudo/image fs (proc, sysfs, tmpfs, squashfs, overlay, ...); one entry per device (bind mounts dropped); system = `/` or under boot/efi; removable = lsblk RM/HOTPLUG or TRAN=usb, else mounted under media; unresponsive network shares listed with zero sizes.
- Unplug mid-scan: scanner stops with `volume_lost` when a listing fails and the root is gone, changed device, or errno is ENODEV/ENXIO/ENOTCONN/ESTALE.
- Unplug on Windows: `OSError.winerror` 21/55/64/433/1006/1167 (not ready, device or network name gone, volume changed, no such device, not connected) count as volume lost, in the scanner and api.
- Paths: pathlib WindowsPath compares case-insensitively, so api volume matching needs no extra code. Volumes mounted in folders (no letter) are reparse points: listed nowhere, never followed.
- Hidden: FILE_ATTRIBUTE_HIDDEN or dot name. Created: st_birthtime (3.12+) else st_ctime.

## WebView2 packaging (Windows)
- pywebview uses the `edgechromium` backend (app.gui_backend); pip pulls pythonnet on Windows.
- The Evergreen WebView2 Runtime ships with Windows 11 and updated Windows 10. app.run checks the EdgeUpdate registry key (HKLM 64/32-bit, HKCU) for `pv` before importing pywebview and shows a native error dialog if missing.
- Installer (Stage 8): bundle or chain the Evergreen Bootstrapper (small, downloads at install time) or the Standalone installer for offline machines; never the Fixed Version runtime (large, no security updates). The app itself makes no network calls.
- WebView2 needs a writable user data folder. pywebview chooses it (private_mode / storage_path). Check it lands in a per-user folder when installed under Program Files, and pass storage_path from platformdirs if not.

