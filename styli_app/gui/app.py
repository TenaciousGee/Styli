"""Starting the window: theme, icon and the main window. styli gui calls run()."""

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QImage, QPalette, QPixmap
from PyQt6.QtWidgets import QApplication

from styli_app import db, paths, privacy
from styli_app.gui import theme
from styli_app.gui.theme import apply_theme
from styli_app.gui.window import MainWindow


def app_icon():
    """Styli's icon: styli.png where Styli is installed, else the copy installed in the icon theme."""
    path = paths.app_root() / "styli.png"
    if path.exists():
        icon = QIcon()
        image = QImage(str(path))
        for size in (16, 24, 32, 48, 64, 128, 256, 512):
            icon.addPixmap(QPixmap.fromImage(image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                                          Qt.TransformationMode.SmoothTransformation)))
        return icon
    return QIcon.fromTheme("styli", QIcon.fromTheme("media-optical"))


def run(con):
    """Start the window: theme, icon and main window. Returns when the window is closed."""
    privacy.private_files()  # covers, photos and caches saved from here on are yours alone
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Styli")
    app.setDesktopFileName("styli")
    app.setWindowIcon(app_icon())
    theme.SYSTEM_PALETTE = QPalette(app.palette())  # before our theme replaces it
    apply_theme(app, db.get_setting(con, "theme", "system"), db.get_setting(con, "tint", "original"))
    win = MainWindow(con)
    hints = app.styleHints()
    if hasattr(hints, "colorSchemeChanged"):  # Qt 6.5+: follow the desktop live
        hints.colorSchemeChanged.connect(win.follow_desktop)
    win.show()
    return app.exec()
