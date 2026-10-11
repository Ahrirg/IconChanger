#!/usr/bin/env bash
# Install Icon Changer for the current user - no root needed.
#   ./install.sh              install or update
#   ./install.sh --uninstall  remove it again
set -euo pipefail

APP=icon-changer
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
LIB="$HOME/.local/lib/$APP"
BIN="$HOME/.local/bin"
PY=/usr/bin/python3

uninstall() {
    if [[ -x "$BIN/$APP" ]]; then
        read -r -p "Give all apps their original icons back first? [Y/n] " answer
        if [[ ! "$answer" =~ ^[Nn] ]]; then
            "$BIN/$APP" --reset-all
        fi
    fi
    if command -v systemctl >/dev/null; then
        systemctl --user disable --now "$APP-sync.path" 2>/dev/null || true
        rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$APP-sync."{path,service}
        systemctl --user daemon-reload 2>/dev/null || true
    fi
    rm -rf "$LIB"
    rm -f "$BIN/$APP" "$DATA/applications/$APP.desktop" "$DATA/icons/hicolor/scalable/apps/$APP.svg"
    echo "Icon Changer removed."
    echo "Your saved images are still in $DATA/$APP - delete that folder if you don't need them."
}

if [[ "${1:-}" == "--uninstall" ]]; then
    uninstall
    exit 0
fi

if ! "$PY" -c "import PyQt6.QtWidgets" 2>/dev/null; then
    echo "Icon Changer needs PyQt6. Install it with:"
    echo "    sudo pacman -S --needed python-pyqt6 qt6-svg"
    exit 1
fi

# Program files
mkdir -p "$LIB" "$BIN" "$DATA/applications" "$DATA/icons/hicolor/scalable/apps"
rm -rf "$LIB/iconchanger"
cp -r "$SRC/iconchanger" "$LIB/"
find "$LIB" -name __pycache__ -prune -exec rm -rf {} +

# Launcher command
cat > "$BIN/$APP" <<EOF
#!/bin/sh
export PYTHONPATH="$LIB\${PYTHONPATH:+:\$PYTHONPATH}"
exec $PY -m iconchanger "\$@"
EOF
chmod 755 "$BIN/$APP"

# Menu entry + icon
cp "$SRC/iconchanger/resources/$APP.svg" "$DATA/icons/hicolor/scalable/apps/$APP.svg"
cat > "$DATA/applications/$APP.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Icon Changer
GenericName=App Icon Editor
Comment=Change the icon of any installed application
Exec="$BIN/$APP"
Icon=$APP
Terminal=false
Categories=Settings;DesktopSettings;
Keywords=icon;icons;launcher;appearance;customize;theme;
StartupWMClass=$APP
EOF

command -v update-desktop-database >/dev/null && update-desktop-database -q "$DATA/applications" || true
if [[ -f "$DATA/icons/hicolor/icon-theme.cache" ]] && command -v gtk-update-icon-cache >/dev/null; then
    gtk-update-icon-cache -q -t -f "$DATA/icons/hicolor" || true
fi

echo "Icon Changer installed."
echo "Open it from your app launcher (search for \"Icon Changer\") or run: $APP"
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "Note: $BIN is not in your PATH, so use the app launcher or run $BIN/$APP" ;;
esac
