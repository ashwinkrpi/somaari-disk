# Contributing

Thanks for helping. Bug reports, tests on new hardware and small focused pull requests are
the most useful.

## Ground rules

- Read-only on the filesystem except Move to Trash (with confirmation). No permanent delete.
- No network access at runtime. No telemetry.
- `core/` is pure Python: all OS access goes through `platform/` (`linux.py`, `windows.py`,
  `fake.py`). The UI (`ui/`) only calls `api/`.
- No Electron, Docker, database, JS framework, Node or build step for the UI.
- New dependencies: permissive licenses only (MIT, BSD, Apache-2.0, PSF, ISC). Explain why in
  `docs/DECISIONS.md` and add a row to `THIRD-PARTY-NOTICES.md`. Open an issue first for
  anything large.

Design and conventions: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
[docs/PLATFORM.md](docs/PLATFORM.md), [design/DESIGN-TOKENS.md](design/DESIGN-TOKENS.md).

## Privacy in issues, tests and screenshots

Never include real file names, folder names, paths, user names or host names. Tests use the
fake platform and `tmp_path`; screenshots use `--demo`. Describe a problem name instead
("a folder name with an emoji"), or reproduce it in demo mode.

## Setup

```sh
python3 -m venv --system-site-packages .venv   # Linux: PyGObject comes from apt
.venv/bin/pip install -e ".[dev]"
.venv/bin/pre-commit install
```

On Linux install the system packages from [INSTALL.md](INSTALL.md) first.

## Before you open a pull request

- Add or update tests for every change. Tests never scan real drives or open windows.
- `pytest -q`, `ruff check .` and `ruff format .` pass.
- Add one line to `CHANGELOG.md`: `- [YYYY-MM-DD] <what> - <why>`.
- Non-obvious choices go in `docs/DECISIONS.md`.
- CI must stay green on Ubuntu and Windows.

Manual checks for UI or platform changes: [docs/TESTING.md](docs/TESTING.md).

By contributing you agree your work is released under the [MIT License](LICENSE).
