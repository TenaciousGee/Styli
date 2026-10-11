#!/bin/sh
# Installs vinyltrack for the current user on Linux.
#   - copies vinyltrack.py and vinyltrack_gui.py to ~/.local/share/vinyltrack/app
#   - creates the `vt` command in ~/.local/bin
#   - adds "Vinyl Cartridge / Stylus Tracker" to your applications menu
# Run it from the folder containing the two .py files:  sh install.sh
set -e

here="$(cd "$(dirname "$0")" && pwd)"
dest="${XDG_DATA_HOME:-$HOME/.local/share}/vinyltrack/app"
bin="$HOME/.local/bin"

mkdir -p "$dest" "$bin"
cp "$here/vinyltrack.py" "$here/vinyltrack_gui.py" "$dest/"
chmod +x "$dest/vinyltrack.py" "$dest/vinyltrack_gui.py"
ln -sf "$dest/vinyltrack.py" "$bin/vt"
echo "Installed the vt command to $bin/vt"

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

python3 "$dest/vinyltrack.py" desktop-entry
echo "Done. Open 'Vinyl Cartridge / Stylus Tracker' from your menu, or run: vt gui"
