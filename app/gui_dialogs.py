"""Dialogs for the Qt GUI: settings, playlist item picker, per-download quality, log."""

from PySide6 import QtCore, QtWidgets

from core import (
    BITRATES,
    CODECS,
    COOKIE_BROWSERS,
    FILENAMES,
    FRAMERATES,
    RESOLUTIONS,
    THEMES,
    format_duration,
    max_quality,
    supported_options,
)


def _combo(choices, current, parent=None):
    combo = QtWidgets.QComboBox(parent)
    for value, label in choices.items():
        combo.addItem(label, value)
    index = combo.findData(current)
    combo.setCurrentIndex(index if index >= 0 else 0)
    return combo


def _buttons(dialog, ok_text="OK"):
    box = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.StandardButton.Ok
        | QtWidgets.QDialogButtonBox.StandardButton.Cancel
    )
    box.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText(ok_text)
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    return box


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(460)
        g = config["general"]
        names = {key: label for key, (label, _) in FILENAMES.items()}

        self.max_downloads = QtWidgets.QSpinBox(minimum=1, maximum=10, value=g["max_downloads"])
        self.filename_video = _combo(names, g["filename_video"])
        self.filename_audio = _combo(names, g["filename_audio"])
        self.playlist_folder = QtWidgets.QCheckBox("Save playlists into their own folder")
        self.playlist_folder.setChecked(g["playlist_folder"])
        self.subtitle_langs = QtWidgets.QLineEdit(g["subtitle_langs"], placeholderText="en.*,de")
        self.cookies_browser = _combo(COOKIE_BROWSERS, g["cookies_browser"])
        self.cookies_browser.setToolTip(
            "Use this browser's login cookies (age-restricted / members-only videos).\n"
            "Close the browser first if it fails.")
        self.clipboard_watch = QtWidgets.QCheckBox("Add links copied to the clipboard")
        self.clipboard_watch.setChecked(g["clipboard_watch"])
        self.notifications = QtWidgets.QCheckBox("Notify when downloads finish or fail")
        self.notifications.setChecked(g["notifications"])
        self.update_ytdlp = QtWidgets.QCheckBox("Update yt-dlp on start-up")
        self.update_ytdlp.setChecked(g["update_ytdlp"])
        self.theme = _combo(THEMES, g["theme"])
        global_args = g["global_args"]
        self.global_args = QtWidgets.QLineEdit(
            global_args if isinstance(global_args, str) else " ".join(global_args),
            placeholderText="e.g. --limit-rate 5M")

        form = QtWidgets.QFormLayout()
        form.addRow("Parallel downloads", self.max_downloads)
        form.addRow("Video file name", self.filename_video)
        form.addRow("Music file name", self.filename_audio)
        form.addRow("", self.playlist_folder)
        form.addRow("Subtitle languages", self.subtitle_langs)
        form.addRow("Browser cookies", self.cookies_browser)
        form.addRow("Theme", self.theme)
        form.addRow("", self.clipboard_watch)
        form.addRow("", self.notifications)
        form.addRow("", self.update_ytdlp)
        form.addRow("Extra yt-dlp args", self.global_args)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(_buttons(self, "Save"))

    def values(self):
        return {
            "max_downloads": self.max_downloads.value(),
            "filename_video": self.filename_video.currentData(),
            "filename_audio": self.filename_audio.currentData(),
            "playlist_folder": self.playlist_folder.isChecked(),
            "subtitle_langs": self.subtitle_langs.text().strip(),
            "cookies_browser": self.cookies_browser.currentData(),
            "clipboard_watch": self.clipboard_watch.isChecked(),
            "notifications": self.notifications.isChecked(),
            "update_ytdlp": self.update_ytdlp.isChecked(),
            "theme": self.theme.currentData(),
            "global_args": self.global_args.text().strip(),
        }


class PlaylistDialog(QtWidgets.QDialog):
    """Pick playlist entries. selected() returns 1-based indices."""

    def __init__(self, job, parent=None):
        super().__init__(parent)
        info = job.info
        self.setWindowTitle("Choose playlist items")
        self.resize(560, 520)
        chosen = {int(i) for i in job.playlist_items.split(",")} if job.playlist_items else None

        self.list = QtWidgets.QListWidget()
        for e in info["entries"]:
            text = f"{e['index']:>3}.  {e['title']}"
            if e["duration"]:
                text += f"   ({format_duration(e['duration'])})"
            item = QtWidgets.QListWidgetItem(text)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, e["index"])
            checked = chosen is None or e["index"] in chosen
            item.setCheckState(QtCore.Qt.CheckState.Checked if checked else QtCore.Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self.list.itemChanged.connect(self.update_count)

        self.count = QtWidgets.QLabel()
        select_all = QtWidgets.QPushButton("All")
        select_none = QtWidgets.QPushButton("None")
        select_all.clicked.connect(lambda: self.set_all(True))
        select_none.clicked.connect(lambda: self.set_all(False))
        self.box = _buttons(self, "Download selected")
        # closing the dialog keeps the whole playlist
        self.box.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("Download all")

        top = QtWidgets.QLabel(f"<b>{info['title'] or 'Playlist'}</b> — {len(info['entries'])} items")
        top.setTextFormat(QtCore.Qt.TextFormat.RichText)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(select_all)
        row.addWidget(select_none)
        row.addWidget(self.count)
        row.addStretch()
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(top)
        layout.addWidget(self.list)
        layout.addLayout(row)
        layout.addWidget(self.box)
        self.update_count()

    def set_all(self, checked):
        state = QtCore.Qt.CheckState.Checked if checked else QtCore.Qt.CheckState.Unchecked
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(state)

    def selected(self):
        return [
            self.list.item(i).data(QtCore.Qt.ItemDataRole.UserRole)
            for i in range(self.list.count())
            if self.list.item(i).checkState() == QtCore.Qt.CheckState.Checked
        ]

    def update_count(self):
        n = len(self.selected())
        self.count.setText(f"{n} selected")
        self.box.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setEnabled(n > 0)


class EditDialog(QtWidgets.QDialog):
    """Change a download's preset and quality; resolutions limited to what the video has."""

    def __init__(self, job, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Edit download")
        formats = (job.info or {}).get("formats") or []
        heights = sorted({h for h, _ in formats}, reverse=True)
        resolutions = {"best": "Best", **{str(h): f"{h}p" for h in heights}} if heights else dict(RESOLUTIONS)
        current = job.options["resolution"]
        if current not in resolutions:
            resolutions[current] = RESOLUTIONS.get(current, current)

        self.preset = QtWidgets.QComboBox()
        self.preset.addItems(list(config["presets"].keys()))
        self.preset.setCurrentText(job.preset)
        self.resolution = _combo(resolutions, current)
        self.fps = _combo(FRAMERATES, job.options["fps"])
        self.codec = _combo(CODECS, job.options["codec"])
        self.bitrate = _combo(BITRATES, job.options["bitrate"])
        self.preset.currentTextChanged.connect(self.refresh_enabled)

        title = QtWidgets.QLabel(job.display_title)
        title.setWordWrap(True)
        form = QtWidgets.QFormLayout()
        if formats:
            form.addRow("Available", QtWidgets.QLabel(f"up to {max_quality(formats)}"))
        form.addRow("Preset", self.preset)
        form.addRow("Resolution", self.resolution)
        form.addRow("FPS", self.fps)
        form.addRow("Codec", self.codec)
        form.addRow("Bitrate", self.bitrate)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(title)
        layout.addLayout(form)
        layout.addWidget(_buttons(self, "Save"))
        self.refresh_enabled()

    def refresh_enabled(self):
        supported = supported_options(self.config, self.preset.currentText())
        for key in ("resolution", "fps", "codec", "bitrate"):
            getattr(self, key).setEnabled(supported[key])

    def values(self):
        return self.preset.currentText(), {
            key: getattr(self, key).currentData() for key in ("resolution", "fps", "codec", "bitrate")
        }


class LogDialog(QtWidgets.QDialog):
    """Live yt-dlp output of one download."""

    def __init__(self, job, parent=None):
        super().__init__(parent)
        self.job = job
        self.shown = 0
        self.setWindowTitle(f"Log — {job.display_title}")
        self.resize(900, 480)
        self.text = QtWidgets.QPlainTextEdit(readOnly=True)
        self.text.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setMaximumBlockCount(5000)
        font = self.text.font()
        font.setFamily("Consolas")
        font.setStyleHint(font.StyleHint.Monospace)
        self.text.setFont(font)
        copy = QtWidgets.QPushButton("Copy all")
        copy.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(self.text.toPlainText()))
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.close)
        row = QtWidgets.QHBoxLayout()
        row.addStretch()
        row.addWidget(copy)
        row.addWidget(close)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.text)
        layout.addLayout(row)
        self.timer = QtCore.QTimer(self, interval=500, timeout=self.refresh)
        self.timer.start()
        self.refresh()

    def refresh(self):
        lines = list(self.job.log)
        if len(lines) < self.shown:
            self.text.clear()
            self.shown = 0
        if len(lines) > self.shown:
            bar = self.text.verticalScrollBar()
            at_end = bar.value() == bar.maximum()
            self.text.appendPlainText("\n".join(lines[self.shown:]))
            self.shown = len(lines)
            if at_end:
                bar.setValue(bar.maximum())
        if not lines and not self.text.toPlainText():
            self.text.setPlainText("(no output yet)")
