# Third-party notices
Add a row here whenever a dependency or bundled asset is added. Permissive licenses only (MIT, BSD, Apache-2.0, PSF-2.0, SIL OFL). Verify the license on the package page when adding.

| Component | Use | License | Bundled? |
|---|---|---|---|
| IBM Plex Sans / Mono (IBM/plex releases: plex-sans 1.1.0, plex-mono 2.5.0) | UI fonts (woff2) | SIL OFL 1.1, license in ui/fonts/OFL.txt | Yes |
| pywebview | Native window | BSD-3-Clause | pip |
| bottle, proxy-tools | pywebview dependencies | MIT | pip |
| typing_extensions | pywebview dependency | PSF-2.0 | pip |
| psutil | Volume info | BSD-3-Clause | pip |
| send2trash | Trash/Recycle Bin | BSD-3-Clause | pip |
| platformdirs | Config/log dirs | MIT | pip |
| pythonnet, clr-loader (Windows) | pywebview WebView2 bridge | MIT | pip; Windows build |
| cffi (Windows) | clr-loader dependency | MIT | pip; Windows build |
| pycparser (Windows) | cffi dependency | BSD-3-Clause | pip; Windows build |
| WebView2 SDK DLLs (Microsoft.Web.WebView2.*, WebView2Loader) | Shipped inside pywebview | Microsoft WebView2 SDK license (BSD-style, redistributable) | Windows build only; pruned from the .deb |
| Python runtime | Frozen into the Windows build | PSF-2.0 | Windows build |
| PyInstaller bootloader | Starts the Windows exe | GPL-2.0 with bootloader exception (output may use any license) | Windows build; build tool otherwise |
| Inno Setup | Windows installer stub | Inno Setup License (permissive, modified-BSD style) | Windows installer; build tool otherwise |
| PyGObject, WebKitGTK (Linux) | Window backend | LGPL | System apt packages, not bundled |
| WebView2 (Windows) | Window backend | Microsoft runtime | Installed separately |

Dev/build-only (not shipped): pytest, ruff, pre-commit, packaging (license test), build, PyInstaller (GPL-2.0 with exception; see docs/DECISIONS.md), Inno Setup compiler, dpkg-deb.

The Linux .deb vendors pywebview, bottle, proxy-tools, typing_extensions, send2trash and platformdirs; psutil, PyGObject and WebKitGTK come from apt.
