"""Entry point: open the UI in a pywebview window."""

from pathlib import Path

UI_DIR = Path(__file__).parent / "ui"


def main() -> None:
    import webview  # lazy: heavy, GUI-only

    webview.create_window("Disk Usage Analyzer", str(UI_DIR / "index.html"))
    webview.start(gui="gtk")


if __name__ == "__main__":
    main()
