# Third-party notices
Add a row here whenever a dependency or bundled asset is added. Permissive licenses only (MIT, BSD, Apache-2.0, SIL OFL). Verify the license on the package page when adding.

| Component | Use | License | Bundled? |
|---|---|---|---|
| IBM Plex Sans / Mono | UI fonts | SIL OFL 1.1 (ship license text in ui/fonts/) | Yes |
| pywebview | Native window | BSD-3-Clause | pip |
| bottle, proxy-tools | pywebview dependencies | MIT | pip |
| typing_extensions | pywebview dependency | PSF-2.0 | pip |
| psutil | Volume info | BSD-3-Clause | pip |
| send2trash | Trash/Recycle Bin | BSD-3-Clause | pip |
| platformdirs | Config/log dirs | MIT | pip |
| PyGObject, WebKitGTK (Linux) | Window backend | LGPL | System apt packages, not bundled |
| WebView2 (Windows) | Window backend | Microsoft runtime | Installed separately |

Dev-only: pytest, ruff, pre-commit (not shipped).
