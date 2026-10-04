"""PyInstaller entry point for the Windows build (GUI only; the CLI ships via pip)."""

import sys

from somaari_disk.__main__ import main

sys.exit(main())
