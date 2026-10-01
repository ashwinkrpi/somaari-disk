import ast
from pathlib import Path

import somaari_disk

PKG = Path(somaari_disk.__file__).parent
FORBIDDEN = {"os", "subprocess", "ctypes", "winreg", "webview", "gi", "psutil", "send2trash"}


def test_version():
    assert somaari_disk.__version__


def test_layers_exist():
    for layer in ("core", "platform", "api", "ui"):
        assert (PKG / layer).is_dir()
    assert (PKG / "ui" / "index.html").is_file()


def test_core_has_no_os_or_gui_imports():
    for py in (PKG / "core").rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert name.split(".")[0] not in FORBIDDEN, f"{py.name} imports {name}"


def test_main_does_not_import_webview_eagerly():
    import sys

    import somaari_disk.__main__  # noqa: F401

    assert "webview" not in sys.modules
