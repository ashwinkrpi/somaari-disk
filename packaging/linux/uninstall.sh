#!/usr/bin/env bash
# Clean uninstall of somaari-disk installed from the .deb (Debian, Ubuntu, Raspberry Pi OS).
# Safe to run again and again: anything already gone is skipped.
#
#   sudo bash packaging/linux/uninstall.sh              remove the app and your app data
#   sudo bash packaging/linux/uninstall.sh --keep-data  remove the app, keep settings/saved scans
#   bash packaging/linux/uninstall.sh --dry-run         show what would be removed
#
# Without sudo it only removes your app data, and says so if the package is still installed.
# Never touches a source checkout or its .venv. See UNINSTALL.md.
set -euo pipefail

PKG=somaari-disk
KEEP_DATA=0
DRY_RUN=0

usage() {
    sed -n '2,10s/^# \{0,1\}//p' "$0"
}

for arg in "$@"; do
    case "$arg" in
        --keep-data) KEEP_DATA=1 ;;
        --dry-run) DRY_RUN=1 ;;
        -h | --help) usage; exit 0 ;;
        *) echo "unknown option: $arg" >&2; usage >&2; exit 2 ;;
    esac
done

say() { printf '%s\n' "$*"; }

IS_ROOT=0
[ "$(id -u)" -eq 0 ] && IS_ROOT=1

# Whose app data to remove: the person who ran sudo, not root.
if [ "$IS_ROOT" -eq 1 ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then
    TARGET_USER=$SUDO_USER
    TARGET_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
    TARGET_USER=$(id -un)
    TARGET_HOME=${HOME:-}
fi
case "$TARGET_HOME" in
    /?*) ;;
    *) echo "cannot find the home folder for $TARGET_USER" >&2; exit 1 ;;
esac

CHANGED=0
remove_path() {
    local path=$1
    [ -e "$path" ] || [ -L "$path" ] || return 0
    if [ "$DRY_RUN" -eq 1 ]; then
        say "would remove: $path"
    else
        rm -rf -- "$path"
        say "removed: $path"
    fi
    CHANGED=1
}

# ---- 1. close open windows of the installed app ---------------------------------
mapfile -t pids < <(pgrep -u "$TARGET_USER" -f "^/usr/bin/python3 /usr/bin/$PKG( |$)" || true)
if [ "${#pids[@]}" -gt 0 ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
        say "would close running $PKG (pid ${pids[*]})"
    else
        say "closing running $PKG (pid ${pids[*]})"
        kill "${pids[@]}" 2>/dev/null || true
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            pgrep -u "$TARGET_USER" -f "^/usr/bin/python3 /usr/bin/$PKG( |$)" >/dev/null || break
            sleep 0.5
        done
    fi
fi

# ---- 2. the package --------------------------------------------------------------
NEEDS_ROOT=0
status=$(dpkg-query -W -f='${db:Status-Abbrev}' "$PKG" 2>/dev/null || true)
case "$status" in
    "" | un*) say "package $PKG: not installed" ;;
    *)
        if [ "$DRY_RUN" -eq 1 ]; then
            say "would run: apt-get purge -y $PKG (needs sudo)"
            CHANGED=1
        elif [ "$IS_ROOT" -eq 0 ]; then
            say "package $PKG is installed: run this script with sudo to remove it"
            NEEDS_ROOT=1
        else
            say "removing package $PKG"
            apt-get purge -y -q "$PKG"
            CHANGED=1
        fi
        ;;
esac

# ---- 3. leftovers no package owns (bytecode, interrupted installs) ----------------
SYSTEM_PATHS=(
    "/usr/lib/$PKG"
    "/usr/bin/$PKG"
    "/usr/share/applications/$PKG.desktop"
    "/usr/share/icons/hicolor/scalable/apps/$PKG.svg"
    "/usr/share/icons/hicolor/256x256/apps/$PKG.png"
    "/usr/share/doc/$PKG"
)
for path in "${SYSTEM_PATHS[@]}"; do
    [ -e "$path" ] || [ -L "$path" ] || continue
    if dpkg -S "$path" >/dev/null 2>&1; then # owned by the package: purge removes it
        [ "$DRY_RUN" -eq 1 ] && say "would remove with the package: $path"
        continue
    fi
    if [ "$IS_ROOT" -eq 1 ] || [ "$DRY_RUN" -eq 1 ]; then
        remove_path "$path"
    else
        say "left over (needs sudo): $path"
        NEEDS_ROOT=1
    fi
done
if [ "$IS_ROOT" -eq 1 ] && [ "$DRY_RUN" -eq 0 ] && [ "$CHANGED" -eq 1 ]; then
    if command -v gtk-update-icon-cache >/dev/null; then
        gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
    fi
    if command -v update-desktop-database >/dev/null; then
        update-desktop-database -q /usr/share/applications || true
    fi
fi

# ---- 4. your app data --------------------------------------------------------------
if [ "$KEEP_DATA" -eq 1 ]; then
    say "keeping app data in $TARGET_HOME/.config/$PKG and $TARGET_HOME/.cache/$PKG"
else
    for path in \
        "$TARGET_HOME/.config/$PKG" \
        "$TARGET_HOME/.cache/$PKG" \
        "$TARGET_HOME/.config/disk-usage-analyzer" \
        "$TARGET_HOME/.cache/disk-usage-analyzer"; do
        remove_path "$path"
    done
fi
# menu entry made by hand for a source checkout (not part of the .deb)
user_menu="$TARGET_HOME/.local/share/applications"
if [ -e "$user_menu/$PKG.desktop" ]; then
    remove_path "$user_menu/$PKG.desktop"
    if [ "$DRY_RUN" -eq 0 ] && command -v update-desktop-database >/dev/null; then
        update-desktop-database -q "$user_menu" || true
    fi
fi

# ---- summary -----------------------------------------------------------------------
if [ "$NEEDS_ROOT" -eq 1 ]; then
    say "not finished: run again with sudo: sudo bash $0"
    exit 1
fi
if [ "$DRY_RUN" -eq 1 ]; then
    say "dry run: nothing was removed"
elif [ "$CHANGED" -eq 1 ]; then
    say "done: $PKG is uninstalled"
else
    say "nothing to remove: $PKG is not installed"
fi
