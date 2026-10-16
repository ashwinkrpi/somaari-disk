"""About section data, per somaari-common/docs/ABOUT_SPEC.md (collector: somaari_about)."""

from __future__ import annotations

from pathlib import Path

from . import __version__

# name=version of these goes into key_dependencies
KEY_PACKAGES = ("pywebview", "platformdirs", "psutil", "send2trash")


def build_info() -> tuple[str | None, str | None]:
    """(commit, date) stamped by packaging/stamp_build.py; (None, None) running from source."""
    try:
        from ._build_info import BUILD_COMMIT, BUILD_DATE
    except ImportError:
        return None, None  # collect() falls back to "dev" / "unknown"
    return BUILD_COMMIT, BUILD_DATE


def about_data(
    app_name: str, *, data_dir: Path | None, config_dir: Path | None, log_dir: Path | None
) -> dict:
    """Debug info (redacted, debug-fields.json order), labels, developer info and the copy text."""
    from somaari_about import as_text, collect, load_debug_fields, load_developer

    commit, date = build_info()
    info = collect(
        app_name,
        __version__,
        build_commit=commit,
        build_date=date,
        packages=KEY_PACKAGES,
        data_dir=data_dir,
        config_dir=config_dir,
        log_dir=log_dir,
        ai_provider=None,  # no AI in this app
        ai_model=None,
    )
    developer = load_developer()
    fields = [{"key": f["key"], "label": f["label"]} for f in load_debug_fields()]
    text = as_text(info, developer)
    return {"info": info, "fields": fields, "developer": developer, "text": text}
