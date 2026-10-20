#!/bin/sh
# Installs Styli for the current user on Linux.
#   - copies styli.py, styli_gui.py and the styli.png icon to ~/.local/share/styli/app
#   - creates the `styli` command (and `vt` as a short name) in ~/.local/bin
#   - adds Styli to your applications menu
#   - moves a library from the app's earlier name (vinyltrack) across, if there is one
# Run it from the folder containing those three files:  sh install.sh
set -e

here="$(cd "$(dirname "$0")" && pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
dest="$data/styli/app"
bin="$HOME/.local/bin"

mkdir -p "$dest" "$bin"
cp "$here/styli.py" "$here/styli_gui.py" "$dest/"
if [ -f "$here/styli.png" ]; then
  cp "$here/styli.png" "$dest/"
else
  echo "Note: styli.png wasn't found next to install.sh, so the menu will use a stock icon."
fi
chmod +x "$dest/styli.py" "$dest/styli_gui.py"
ln -sf "$dest/styli.py" "$bin/styli"
ln -sf "$dest/styli.py" "$bin/vt"
echo "Installed the styli command to $bin/styli (vt still works too)"

case ":$PATH:" in
  *":$bin:"*) ;;
  *) echo "Note: $bin is not on your PATH. Add this line to ~/.profile, then log out and back in:"
     echo "  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

if python3 -c "import PyQt6.QtWidgets" 2>/dev/null; then
  echo "PyQt6 found."
else
  echo "PyQt6 is not installed yet, so the window won't open until it is. Install it with:"
  echo "  Debian/Ubuntu/Mint: sudo apt install python3-pyqt6"
  echo "  Fedora:             sudo dnf install python3-pyqt6"
  echo "  Arch/Manjaro:       sudo pacman -S python-pyqt6"
fi

if python3 -c "import keyring" 2>/dev/null; then
  echo "keyring found: a linked Discogs token is kept in your system keyring."
else
  echo "Optional: install keyring so a linked Discogs token is kept in your system keyring:"
  echo "  Debian/Ubuntu/Mint: sudo apt install python3-keyring"
  echo "  Fedora:             sudo dnf install python3-keyring"
  echo "  Arch/Manjaro:       sudo pacman -S python-keyring"
fi

# Adds the menu entry and icon, and on first run moves an existing library into Styli's folder.
python3 "$dest/styli.py" desktop-entry

# Remove the program files installed under the old name. Your data is only ever moved
# by Styli itself, and the old folder is removed only if nothing else is left in it.
old="$data/vinyltrack"
if [ -d "$old/app" ]; then
  rm -f "$old/app/vinyltrack.py" "$old/app/vinyltrack_gui.py"
  rm -rf "$old/app/__pycache__"
  rmdir "$old/app" 2>/dev/null || true
  rmdir "$old" 2>/dev/null || true
fi

echo "Done. Open Styli from your menu, or run: styli gui"
