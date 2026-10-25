#!/bin/sh
# Installs Styli for the current user on Linux.
#   - copies Styli (styli.py, the styli_app folder and the styli.png icon) to ~/.local/share/styli/app
#   - creates the `styli` command in ~/.local/bin
#   - adds Styli to your applications menu
#   - moves a library from the app's earlier name (vinyltrack) across, if there is one
#   - checks for PyQt6 (needed for the window) and keyring (optional, keeps a Discogs token safe)
# Run it from the folder containing those files:  sh install.sh
# Running it again updates Styli in place; your library and settings are never touched.
set -e  # stop at the first thing that fails

# Where things go: the program in Styli's own folder, the commands in ~/.local/bin.
here="$(cd "$(dirname "$0")" && pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
dest="$data/styli/app"
bin="$HOME/.local/bin"

# Copy the program and icon, and make the `styli` command point at it.
if [ ! -f "$here/styli_app/__init__.py" ]; then
  echo "The styli_app folder wasn't found next to install.sh. Download the whole project and try again."
  exit 1
fi
mkdir -p "$dest" "$bin"
rm -rf "$dest/styli_app" "$dest/__pycache__"   # replaced as a whole, so no old modules are left behind
rm -f "$dest/styli_gui.py"                      # the window lived in this one file before version 1.11
cp "$here/styli.py" "$dest/"
cp -R "$here/styli_app" "$dest/"
find "$dest/styli_app" -name "__pycache__" -type d -prune -exec rm -rf {} +
if [ -f "$here/styli.png" ]; then
  cp "$here/styli.png" "$dest/"
else
  echo "Note: styli.png wasn't found next to install.sh, so the menu will use a stock icon."
fi
chmod +x "$dest/styli.py"
ln -sf "$dest/styli.py" "$bin/styli"
echo "Installed the styli command to $bin/styli"

# Earlier versions also made a `vt` short name. Remove it, but only if it's Styli's own link.
if [ -L "$bin/vt" ] && [ "$(readlink "$bin/vt")" = "$dest/styli.py" ]; then
  rm -f "$bin/vt"
fi

# ~/.local/bin has to be on your PATH for the commands to work from a terminal.
case ":$PATH:" in
  *":$bin:"*) ;;
  *) echo "Note: $bin is not on your PATH. Add this line to ~/.profile, then log out and back in:"
     echo "  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

# The window needs PyQt6. The commands work without it.
if python3 -c "import PyQt6.QtWidgets" 2>/dev/null; then
  echo "PyQt6 found."
else
  echo "PyQt6 is not installed yet, so the window won't open until it is. Install it with:"
  echo "  Debian/Ubuntu/Mint: sudo apt install python3-pyqt6"
  echo "  Fedora:             sudo dnf install python3-pyqt6"
  echo "  Arch/Manjaro:       sudo pacman -S python-pyqt6"
fi

# Optional: with keyring, a linked Discogs token goes in the system keyring rather than the database.
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
