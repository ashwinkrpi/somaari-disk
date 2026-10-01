"""Entry point: open the UI in a pywebview window, or `scan <path>` / `volumes` on the CLI."""

import sys
from pathlib import Path

UI_DIR = Path(__file__).parent / "ui"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["scan"]:
        from .cli import main as cli_main

        return cli_main(args[1:])
    if args[:1] == ["volumes"]:
        from .cli import volumes_main

        return volumes_main(args[1:])

    import webview  # lazy: heavy, GUI-only

    webview.create_window("Disk Usage Analyzer", str(UI_DIR / "index.html"))
    webview.start(gui="gtk")
    return 0


if __name__ == "__main__":
    sys.exit(main())
