# PyInstaller spec for the Windows folder build: pyinstaller packaging/windows/somaari-disk.spec
# Output: dist/somaari-disk/somaari-disk.exe (+ _internal/). pywebview ships its own PyInstaller hook.
import sys
from pathlib import Path

import somaari_disk
from PyInstaller.utils.hooks import collect_data_files

HERE = Path(SPECPATH)  # noqa: F821 (defined by PyInstaller)
sys.path.insert(0, str(HERE.parent))
from stamp_build import stamp  # noqa: E402

stamp(Path(somaari_disk.__file__).parent)  # build commit + date for the About section
ICON = str(HERE.parent / "icons" / "somaari-disk.ico")

a = Analysis(  # noqa: F821
    [str(HERE / "launch.py")],
    datas=collect_data_files("somaari_disk") + collect_data_files("somaari_about"),
    hiddenimports=[
        "somaari_disk._build_info",
        "somaari_disk.platform.windows",
        "webview.platforms.edgechromium",
    ],
    excludes=["tkinter", "gi", "PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy"],
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="somaari-disk",
    icon=ICON,
    console=False,
    upx=False,
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="somaari-disk", upx=False)  # noqa: F821
