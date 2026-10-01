# Platform notes (Windows must implement before public)
- Volumes: drive letters via GetLogicalDrives; type via GetDriveType (fixed/removable/network/CD); label via GetVolumeInformation; usage via GetDiskFreeSpaceEx.
- Paths: long paths (\\?\ prefix), case-insensitive compare, no assumption of "/".
- Reparse points/junctions: detect via attributes; never follow.
- Size on disk: GetCompressedFileSize / cluster rounding; hard links via file index.
- Hidden/system: FILE_ATTRIBUTE_HIDDEN/SYSTEM.
- Owner/perms: simplified display (owner name + read-only flag).
- Trash: Recycle Bin via send2trash. Reveal: `explorer /select,<path>`.
- Packaging: WebView2 runtime check.

## Interface status (Stage 2)
| Method | Linux | Windows (Stage 7) |
|---|---|---|
| stat / scandir | os.scandir, st_blocks, (dev, ino) | shared; needs file index + compressed size |
| list_volumes | psutil mount table + `lsblk -J` (3 s timeout); network statvfs in a thread (2 s) | stub: NotImplementedError |
| owner_and_perms | lstat + pwd/grp, stat.filemode, os.access(W_OK) | stub |
| is_hidden | dot name | FILE_ATTRIBUTE_HIDDEN or dot name (done) |
| trash | send2trash (shared): home trash or per-volume `.Trash-<uid>`; refuses volume roots and read-only folders | shared (Recycle Bin), untested |
| reveal | `xdg-open` on the folder (file: its parent; no selection) | stub |
- Volume rules: skip pseudo/image fs (proc, sysfs, tmpfs, squashfs, overlay, ...); one entry per device (bind mounts dropped); system = `/` or under boot/efi; removable = lsblk RM/HOTPLUG or TRAN=usb, else mounted under media; unresponsive network shares listed with zero sizes.
- Unplug mid-scan: scanner stops with `volume_lost` when a listing fails and the root is gone, changed device, or errno is ENODEV/ENXIO/ENOTCONN/ESTALE.
