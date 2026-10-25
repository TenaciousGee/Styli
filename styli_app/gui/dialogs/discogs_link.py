"""Linking a Discogs account with a personal access token."""

from PyQt6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLineEdit

from styli_app import discogs, web
from styli_app.gui.tasks import run_task
from styli_app.gui.theme import C
from styli_app.gui.widgets import button, dialog_layout, label, restyle


class DiscogsLinkDialog(QDialog):
    """Link a Discogs account with a personal access token."""

    def __init__(self, parent, main):
        super().__init__(parent)
        self.main, self.tasks, self.closed, self.user = main, set(), False, None
        v = dialog_layout(self, "Link your Discogs account", 560)
        v.addWidget(label("Link your Discogs account", "h2"))
        v.addWidget(label("Linking lets you pick records straight from your Discogs collection when adding them.",
                          "muted", wrap=True))
        step1 = label('1.  Open your <a href="{0}" style="color:{1}">Discogs developer settings</a> and click '
                      '<b>Generate new token</b>.'.format(discogs.DISCOGS_TOKEN_PAGE, C["accent"]), wrap=True)
        step1.setOpenExternalLinks(True)
        v.addWidget(step1)
        v.addWidget(label("2.  Copy the token and paste it here.", wrap=True))
        row = QHBoxLayout()
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("Personal access token")
        self.token.returnPressed.connect(self.link)
        row.addWidget(self.token, 1)
        show = QCheckBox("Show")
        show.toggled.connect(lambda on: self.token.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        row.addWidget(show)
        v.addLayout(row)
        v.addWidget(label("The token only lets this app read your Discogs data. It's kept in your library file on "
                          "this computer. You can unlink in Preferences, and delete the token on Discogs at any time.",
                          "muted", wrap=True))
        self.status = label("", "muted", wrap=True)
        v.addWidget(self.status)
        v.addStretch(1)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(button("Cancel", self.reject))
        self.link_btn = button("Link account", self.link, "primary")
        bottom.addWidget(self.link_btn)
        v.addLayout(bottom)

    def done(self, result):
        """Mark the dialog closed so a token check that finishes later is ignored."""
        self.closed = True
        super().done(result)

    def link(self):
        """Check the pasted token with Discogs, then save it (in the system keyring if there is one)."""
        token = self.token.text().strip()
        if not token:
            self.status.setText("Paste your token first.")
            restyle(self.status, "warn")
            return
        self.link_btn.setEnabled(False)
        self.status.setText("Checking the token with Discogs...")
        restyle(self.status, "muted")

        def ok(user):
            if self.closed:
                return
            discogs.save_discogs_account(self.main.con, token, user)  # into the system keyring if there is one
            self.user = user
            self.main.update_discogs_tab()
            self.accept()

        def failed(e):
            if not self.closed:
                self.link_btn.setEnabled(True)
                self.status.setText(str(e) if isinstance(e, web.MBError) else repr(e))
                restyle(self.status, "warn")

        run_task(lambda: discogs.discogs_identity(token), ok, failed, self.tasks)
