"""Preferences: theme and tint (previewed as you choose), your Discogs account and the cover cache."""

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QDialog, QFrame, QGridLayout, QHBoxLayout, QMessageBox,
                             QPushButton, QRadioButton, QScrollArea, QVBoxLayout, QWidget)

from styli_app import covers, discogs
from styli_app.gui.dialogs.discogs_link import DiscogsLinkDialog
from styli_app.gui.theme import C, THEMES, TINTS, TINT_NAMES, system_is_dark, tint_swatch
from styli_app.gui.widgets import button, dialog_layout, label


class PrefsDialog(QDialog):
    """Appearance and album cover settings. Theme changes preview straight away."""

    def __init__(self, main):
        super().__init__(main)
        self.main, self.app = main, QApplication.instance()
        self.original, self.original_tint = main.theme_pref, main.tint
        outer = dialog_layout(self, "Preferences", 560)
        self.resize(640, 820)
        outer.addWidget(label("Preferences", "h2"))
        scroll = QScrollArea()  # scrolls on smaller screens; Cancel and Save stay in view
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("detailInner")
        inner.setStyleSheet("QWidget#detailInner { background: transparent; }")
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 12, 0)
        v.setSpacing(12)

        v.addSpacing(4)
        v.addWidget(label("APPEARANCE", "caption"))
        self.group = QButtonGroup(self)
        detected = "dark" if system_is_dark(self.app) else "light"
        hints = {"system": "Follows your desktop's light or dark setting, and changes when it does. "
                           "Your desktop is currently {}.".format(detected),
                 "light": "", "dark": ""}
        for key, text in THEMES.items():
            rb = QRadioButton(text)
            rb.setProperty("key", key)
            rb.setChecked(key == main.theme_pref)
            self.group.addButton(rb)
            v.addWidget(rb)
            if hints[key]:
                h = label(hints[key], "muted", wrap=True)
                h.setContentsMargins(27, 0, 0, 4)
                v.addWidget(h)
        self.group.buttonToggled.connect(self.preview)

        v.addSpacing(10)
        v.addWidget(label("COLOUR TINT", "caption"))
        tint_row = QHBoxLayout()
        self.tint_name = label("", "lead")
        tint_row.addWidget(self.tint_name)
        tint_row.addStretch(1)
        v.addLayout(tint_row)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        self.tints = QButtonGroup(self)
        keys = ["original"] + [k for k, _, _ in TINTS] + ["black", "white"]
        for n, key in enumerate(keys):
            b = QPushButton()
            b.setCheckable(True)
            b.setFixedSize(34, 34)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(TINT_NAMES[key])
            b.setProperty("key", key)
            b.setAccessibleName("Colour tint: " + TINT_NAMES[key])
            b.setChecked(key == main.tint)
            self.tints.addButton(b)
            grid.addWidget(b, n // 10, n % 10)
        grid.setColumnStretch(10, 1)
        v.addLayout(grid)
        v.addWidget(label("Pastels colour the buttons, highlights and a faint wash over the backgrounds. Pure "
                          "black gives true-black backgrounds in dark mode, and pure white gives pure-white "
                          "backgrounds in light mode.", "muted", wrap=True))
        self.tints.buttonClicked.connect(lambda b: self.preview_tint(b.property("key")))
        self._style_swatches()

        v.addSpacing(10)
        v.addWidget(label("ALBUM COVERS", "caption"))
        folder = covers.library_cover_path(0).parent
        v.addWidget(label("Each album in your library has its cover saved here, so it shows offline and "
                          "stays put even if temporary files are cleared:", "muted", wrap=True))
        path = label(str(folder), wrap=True)
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(path)
        row = QHBoxLayout()
        row.addWidget(button("Open folder", lambda: (folder.mkdir(parents=True, exist_ok=True),
                                                     QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))))))
        row.addWidget(button("Save missing covers now", self.fetch_missing))
        row.addStretch(1)
        v.addLayout(row)
        self.cover_msg = label("", "muted", wrap=True)
        v.addWidget(self.cover_msg)

        v.addSpacing(10)
        v.addWidget(label("DISCOGS", "caption"))
        self.dg_status = label("", "muted", wrap=True)
        v.addWidget(self.dg_status)
        drow = QHBoxLayout()
        self.dg_link_btn = button("Link Discogs account", self.link_discogs)
        self.dg_unlink_btn = button("Unlink", self.unlink_discogs, "danger")
        drow.addWidget(self.dg_link_btn)
        drow.addWidget(self.dg_unlink_btn)
        drow.addStretch(1)
        v.addLayout(drow)
        self._show_discogs()

        v.addSpacing(10)
        v.addWidget(label("SEARCH PREVIEWS", "caption"))
        self.cache_label = label("", "muted", wrap=True)
        v.addWidget(self.cache_label)
        row2 = QHBoxLayout()
        row2.addWidget(button("Clear preview images", self.clear_cache))
        row2.addStretch(1)
        v.addLayout(row2)
        self._show_cache()

        v.addStretch(1)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(button("Cancel", self.reject))
        save = button("Save", self.save, "primary")
        save.setDefault(True)
        bottom.addWidget(save)
        outer.addLayout(bottom)

    def _show_discogs(self):
        """Show whether a Discogs account is linked and where its token is kept."""
        token, user = discogs.discogs_account(self.main.con)
        if token:
            text = "Linked as {}. Your collection has its own Discogs tab. {}".format(
                user, discogs.token_storage_note(self.main.con))
        elif discogs.DISCOGS_PROBLEM:
            text = discogs.DISCOGS_PROBLEM
        else:
            text = "Not linked. Link your account to pick records from your Discogs collection."
        self.dg_status.setText(text)
        self.dg_status.setWordWrap(True)
        self.dg_link_btn.setText("Use a different token" if token or discogs.DISCOGS_PROBLEM else "Link Discogs account")
        self.dg_unlink_btn.setVisible(bool(token or discogs.DISCOGS_PROBLEM))

    def link_discogs(self):
        """Link (or relink) a Discogs account."""
        if DiscogsLinkDialog(self, self.main).exec():
            self._show_discogs()

    def unlink_discogs(self):
        """Unlink the Discogs account, removing its token (after asking)."""
        if QMessageBox.question(self, "Unlink Discogs", "Unlink your Discogs account? The access token is removed "
                                "from this computer. Records you've already added stay in your library."
                                ) == QMessageBox.StandardButton.Yes:
            discogs.discogs_unlink(self.main.con)
            self.main.update_discogs_tab()
            self._show_discogs()

    def _choice(self):
        """The theme chosen: system, light or dark."""
        b = self.group.checkedButton()
        return b.property("key") if b else "system"

    def preview(self, btn, checked):
        """Preview a theme as soon as it's chosen."""
        if checked:
            self.main.set_theme(self._choice(), save=False)
            self._style_swatches()

    def _tint_choice(self):
        """The colour tint chosen."""
        b = self.tints.checkedButton()
        return b.property("key") if b else "original"

    def preview_tint(self, key):
        """Preview a tint as soon as it's chosen."""
        self.main.set_theme(self._choice(), save=False, tint=key)
        self._style_swatches()

    def _style_swatches(self):
        """Round colour swatches; the chosen one gets a ring in the text colour."""
        for b in self.tints.buttons():
            colour = tint_swatch(b.property("key"))
            edge = C["border"] if colour.lower() in ("#000000", "#ffffff") else "transparent"
            b.setStyleSheet("QPushButton {{ background: {0}; border: 1px solid {1}; border-radius: 17px; padding: 0; }}"
                            "QPushButton:hover {{ border: 2px solid {2}; }}"
                            "QPushButton:checked {{ border: 3px solid {3}; }}".format(colour, edge, C["faint"], C["text"]))
        self.tint_name.setText(TINT_NAMES.get(self._tint_choice(), ""))

    def fetch_missing(self):
        """Fetch covers for every library record that doesn't have one yet."""
        n = self.main.prefetch_covers()
        self.cover_msg.setText("Fetching {} cover{} in the background. They appear as they arrive.".format(
            n, "" if n == 1 else "s") if n else "Every album already has its cover saved, or none is available.")

    def _show_cache(self):
        """Show how much space cover previews use."""
        mb = covers.search_cache_size() / (1024 * 1024)
        self.cache_label.setText("Cover previews from searches are kept for a while so browsing is quicker. "
                                 "They use {:.1f} MB. Your albums' own covers are not affected by clearing them."
                                 .format(mb))

    def clear_cache(self):
        """Delete the cover previews (your albums' own covers are kept)."""
        self.main.covers.cancel_queued()
        covers.clear_search_cache()
        self.main.covers.forget_previews()
        self._show_cache()

    def save(self):
        """Keep the chosen theme and tint."""
        self.main.set_theme(self._choice(), save=True, tint=self._tint_choice())
        self.accept()

    def reject(self):
        """Cancel: put back the theme and tint you had before previewing others."""
        self.main.set_theme(self.original, save=False, tint=self.original_tint)
        super().reject()
