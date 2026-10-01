# Platform notes (Windows must implement before public)
- Volumes: drive letters via GetLogicalDrives; type via GetDriveType (fixed/removable/network/CD); label via GetVolumeInformation; usage via GetDiskFreeSpaceEx.
- Paths: long paths (\\?\ prefix), case-insensitive compare, no assumption of "/".
- Reparse points/junctions: detect via attributes; never follow.
- Size on disk: GetCompressedFileSize / cluster rounding; hard links via file index.
- Hidden/system: FILE_ATTRIBUTE_HIDDEN/SYSTEM.
- Owner/perms: simplified display (owner name + read-only flag).
- Trash: Recycle Bin via send2trash. Reveal: `explorer /select,<path>`.
- Packaging: WebView2 runtime check.
