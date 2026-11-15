import logging
import sys
from pathlib import Path

import qtawesome as qta
from dep_dl import DepWorker
from PySide6 import QtCore, QtGui, QtWidgets
from ui.main_window import Ui_MainWindow

import manager as mgr
from core import (
    BIN_DIR,
    BITRATES,
    CODECS,
    CONFIG_PATH,
    FRAMERATES,
    OPTION_DEFAULTS,
    RESOLUTIONS,
    ROOT,
    find_urls,
    format_duration,
    load_config,
    open_path,
    reveal_path,
    save_toml,
    supported_options,
)
from gui_dialogs import EditDialog, LogDialog, PlaylistDialog, SettingsDialog
from utils import ItemRoles, TreeColumn

__version__ = ""
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s (%(module)s:%(lineno)d) %(message)s",
    handlers=[
        logging.FileHandler(ROOT / "debug.log", encoding="utf-8", delay=True),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

COMBO_OPTIONS = {
    "resolution": ("Resolution", RESOLUTIONS, "Maximum video resolution (closest available is used)"),
    "fps": ("FPS", FRAMERATES, "Maximum video frame rate (closest available is used)"),
    "codec": ("Codec", CODECS, "Preferred video codec. H.264 plays everywhere but YouTube "
                               "only offers it up to 1080p"),
    "bitrate": ("Bitrate", BITRATES, "mp3 bitrate"),
}
CHECK_OPTIONS = {
    "subtitles": ("Subtitles", "Download and embed subtitles (video presets)"),
    "sponsorblock": ("SponsorBlock", "Cut sponsor / self-promo segments; for music also non-music parts"),
    "save_cover": ("Save cover file", "Also keep the thumbnail as a separate .jpg"),
    "normalize": ("Normalize volume", "Even out loudness (mp3 and other re-encoded audio)"),
    "clean_titles": ("Clean music titles",
                     'Split "Artist - Title" and drop "(Official Video)", "[Lyrics]"… (audio presets)'),
}
STATUS_COLORS = {
    mgr.FINISHED: "#2e9d4f",
    mgr.ERROR: "#d64545",
    mgr.PAUSED: "#b08a2e",
    mgr.CONVERTING: "#9b59b6",
}


class Bridge(QtCore.QObject):
    """Carries manager events (from worker threads) to the UI thread."""

    event = QtCore.Signal(str, object)


class MainWindow(QtWidgets.QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.setWindowIcon(QtGui.QIcon(str(ROOT / "assets" / "yt-dlp-gui.ico")))
        self.pb_add.setIcon(qta.icon("mdi6.plus"))
        self.pb_add.setIconSize(QtCore.QSize(21, 21))
        self.pb_clear.setIcon(qta.icon("mdi6.broom"))
        self.pb_clear.setIconSize(QtCore.QSize(22, 22))
        self.pb_clear.setToolTip("Clear finished and failed downloads")
        self.pb_download.setIcon(qta.icon("mdi6.download"))
        self.pb_download.setIconSize(QtCore.QSize(22, 22))
        self.pb_download.setToolTip("Start everything queued")
        self.te_link.setPlaceholderText(
            "Paste or drop video / playlist links here, one per line\n"
            "https://www.youtube.com/watch?v=hTWKbfoikeg\n"
            "https://www.youtube.com/playlist?list=..."
        )

        try:
            self.config = load_config()
        except Exception:
            logger.error("Config file error.", exc_info=True)
            QtWidgets.QMessageBox.critical(self, "Application Message", "Config file error.")
            sys.exit(1)

        self.bridge = Bridge()
        self.bridge.event.connect(self.on_event, QtCore.Qt.ConnectionType.QueuedConnection)
        self.manager = mgr.DownloadManager(self.config, self.bridge.event.emit)
        self.items = {}  # job id -> QTreeWidgetItem
        self.log_dialogs = {}

        self.setup_option_rows()
        self.setup_tree()
        self.setup_menus()
        self.load_settings()
        self.connect_ui()
        self.tray = QtWidgets.QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("yt-dlp-gui")
        self.apply_theme()
        self.setAcceptDrops(True)
        self.te_link.setFocus()
        self.pb_download.setEnabled(False)
        self.show()

        self.manager.load_history()
        self.update_summary()

        self.dep_worker = DepWorker(self.config["general"]["update_ytdlp"])
        self.dep_worker.finished.connect(self.on_dep_finished)
        self.dep_worker.progress.connect(self.on_dep_progress)
        self.dep_worker.start()

    # ---- setup -----------------------------------------------------------------------

    def setup_option_rows(self):
        """Quality dropdowns (row 1) and extra options (row 3) around the "Save to" row."""
        self.option_widgets = {}
        quality = QtWidgets.QHBoxLayout()
        for key, (label, choices, tip) in COMBO_OPTIONS.items():
            combo = QtWidgets.QComboBox(self.gb_params)
            for value, text in choices.items():
                combo.addItem(text, value)
            combo.setToolTip(tip)
            lb = QtWidgets.QLabel(label, self.gb_params)
            lb.setBuddy(combo)
            quality.addWidget(lb)
            quality.addWidget(combo)
            quality.addSpacing(10)
            self.option_widgets[key] = combo
        quality.addStretch()
        self.gridLayout.addWidget(QtWidgets.QLabel("Quality", self.gb_params), 1, 0, 1, 1)
        self.gridLayout.addLayout(quality, 1, 1, 1, 4)

        extras = QtWidgets.QHBoxLayout()
        for key, (label, tip) in CHECK_OPTIONS.items():
            box = QtWidgets.QCheckBox(label, self.gb_params)
            box.setToolTip(tip)
            extras.addWidget(box)
            self.option_widgets[key] = box
        extras.addStretch()
        self.gridLayout.addWidget(QtWidgets.QLabel("Options", self.gb_params), 3, 0, 1, 1)
        self.gridLayout.addLayout(extras, 3, 1, 1, 4)

    def setup_tree(self):
        self.tw.headerItem().setText(TreeColumn.PRESET, "Format")
        self.tw.setColumnWidth(TreeColumn.TITLE, 280)
        self.tw.setColumnWidth(TreeColumn.PRESET, 150)
        self.tw.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tw.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.tw.setAlternatingRowColors(True)

    def setup_menus(self):
        self.action_settings = QtGui.QAction(qta.icon("mdi6.cog-outline"), "Settings…", self)
        self.action_settings.setShortcut(QtGui.QKeySequence("Ctrl+,"))
        self.action_open_downloads = QtGui.QAction(qta.icon("mdi6.folder-open-outline"),
                                                   "Open Download Folder", self)
        first = self.menuFile.actions()[0] if self.menuFile.actions() else None
        self.menuFile.insertAction(first, self.action_settings)
        self.menuFile.insertAction(first, self.action_open_downloads)
        self.menuFile.insertSeparator(first)

        self.action_pause_all = QtGui.QAction("Pause All", self)
        self.action_resume_all = QtGui.QAction("Resume All", self)
        self.action_clear_done = QtGui.QAction("Clear Finished && Failed", self)
        self.action_clear_all = QtGui.QAction("Clear All", self)
        self.menuEdit.addSeparator()
        for action in (self.action_pause_all, self.action_resume_all,
                       self.action_clear_done, self.action_clear_all):
            self.menuEdit.addAction(action)

        # keyboard shortcuts on the downloads list
        for key, slot in (("Delete", self.remove_selected), ("Return", self.open_selected),
                          ("Space", self.toggle_pause_selected)):
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(key), self.tw)
            shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(slot)

    def connect_ui(self):
        self.dd_preset.currentIndexChanged.connect(self.refresh_option_state)
        self.pb_path.clicked.connect(self.button_path)
        self.pb_add.clicked.connect(self.button_add)
        self.pb_clear.clicked.connect(lambda: self.manager.clear())
        self.pb_download.clicked.connect(self.button_download)
        self.tw.customContextMenuRequested.connect(self.open_menu)
        self.tw.itemDoubleClicked.connect(lambda item, col: self.open_file(self.job_for(item)))
        self.tw.itemSelectionChanged.connect(self.show_selected_details)

        self.action_settings.triggered.connect(self.show_settings)
        self.action_open_downloads.triggered.connect(
            lambda: self.le_path.text() and open_path(self.le_path.text()))
        self.action_open_bin_folder.triggered.connect(lambda: self.open_folder(BIN_DIR))
        self.action_open_log_folder.triggered.connect(lambda: self.open_folder(ROOT))
        self.action_exit.triggered.connect(self.close)
        self.action_about.triggered.connect(self.show_about)
        self.action_clear_url_list.triggered.connect(self.te_link.clear)
        self.action_pause_all.triggered.connect(
            lambda: [self.manager.pause(j) for j in list(self.manager.jobs.values())
                     if j.active or j.status == mgr.WAITING])
        self.action_resume_all.triggered.connect(
            lambda: self.manager.start([j for j in self.manager.jobs.values() if j.status == mgr.PAUSED]))
        self.action_clear_done.triggered.connect(lambda: self.manager.clear())
        self.action_clear_all.triggered.connect(self.clear_all)
        QtWidgets.QApplication.clipboard().dataChanged.connect(self.on_clipboard)

    def load_settings(self):
        g = self.config["general"]
        self.dd_preset.addItems(list(self.config["presets"].keys()))
        current = g["current_preset"]
        self.dd_preset.setCurrentIndex(current if 0 <= current < self.dd_preset.count() else 0)
        self.le_path.setText(g["path"])
        for key, widget in self.option_widgets.items():
            if isinstance(widget, QtWidgets.QComboBox):
                widget.setCurrentIndex(max(0, widget.findData(g[key])))
            else:
                widget.setChecked(g[key])
        self.refresh_option_state()

    def apply_theme(self):
        theme = self.config["general"]["theme"]
        app = QtWidgets.QApplication.instance()
        if theme == "dark" and app.style().name().lower() == "windowsvista":
            app.setStyle("Fusion")  # the classic Windows style has no dark mode
        scheme = {"light": QtCore.Qt.ColorScheme.Light,
                  "dark": QtCore.Qt.ColorScheme.Dark}.get(theme, QtCore.Qt.ColorScheme.Unknown)
        try:
            QtGui.QGuiApplication.styleHints().setColorScheme(scheme)
        except AttributeError:  # Qt < 6.8
            pass

    # ---- options -------------------------------------------------------------------

    def refresh_option_state(self):
        supported = supported_options(self.config, self.dd_preset.currentText())
        for key, widget in self.option_widgets.items():
            widget.setEnabled(supported[key])

    def current_options(self):
        options = {}
        for key in OPTION_DEFAULTS:
            widget = self.option_widgets[key]
            options[key] = widget.currentData() if isinstance(widget, QtWidgets.QComboBox) else widget.isChecked()
        return options

    # ---- dependencies --------------------------------------------------------------

    def on_dep_progress(self, status):
        self.statusBar.showMessage(status, 10000)

    def on_dep_finished(self):
        self.dep_worker.deleteLater()
        self.pb_download.setEnabled(True)
        self.update_summary()

    # ---- manager events --------------------------------------------------------------

    def on_event(self, event, job):
        if event == "added":
            self.add_item(job)
        elif event == "updated":
            if job.id in self.items:
                self.update_item(job)
        elif event == "removed":
            item = self.items.pop(job.id, None)
            if item is not None:
                self.tw.takeTopLevelItem(self.tw.indexOfTopLevelItem(item))
        elif event == "playlist":
            self.choose_playlist_items(job)
        elif event in ("finished", "failed"):
            self.notify_done(event, job)
        self.update_summary()

    def add_item(self, job):
        item = QtWidgets.QTreeWidgetItem(self.tw, ["", "", "-", "", "", "-", "-"])
        pb = QtWidgets.QProgressBar()
        pb.setStyleSheet("QProgressBar { margin-bottom: 3px; }")
        pb.setTextVisible(False)
        self.tw.setItemWidget(item, TreeColumn.PROGRESS, pb)
        for column in range(1, 7):
            item.setTextAlignment(column, QtCore.Qt.AlignmentFlag.AlignCenter)
        item.setData(0, ItemRoles.IdRole, job.id)
        self.items[job.id] = item
        self.update_item(job)

    def update_item(self, job):
        item = self.items[job.id]
        title = job.display_title
        if job.is_playlist:
            total = len(job.info["entries"])
            chosen = len(job.playlist_items.split(",")) if job.playlist_items else total
            title += f"  [{chosen}/{total}]" if chosen != total else f"  [{total} items]"
        elif job.info and job.info.get("duration"):
            title += f"  ({format_duration(job.info['duration'])})"
        status = job.status
        if job.awaiting_choice:
            status = "Choose items"
        elif job.status == mgr.DOWNLOADING and job.count and job.count > 1:
            status += f" {job.item}/{job.count}"

        item.setText(TreeColumn.TITLE, title)
        item.setText(TreeColumn.PRESET, self.manager.label(job))
        item.setText(TreeColumn.SIZE, job.size or "-")
        item.setText(TreeColumn.STATUS, status)
        item.setText(TreeColumn.SPEED, job.speed or "-")
        item.setText(TreeColumn.ETA, job.eta or "-")
        color = STATUS_COLORS.get(job.status)
        item.setForeground(TreeColumn.STATUS, QtGui.QBrush(QtGui.QColor(color)) if color else QtGui.QBrush())

        tip = job.url + (f"\n{job.filepath}" if job.filepath else "")
        item.setToolTip(TreeColumn.TITLE, tip)
        item.setToolTip(TreeColumn.STATUS, job.error or job.probe_error or "")

        pb = self.tw.itemWidget(item, TreeColumn.PROGRESS)
        try:
            percent = 100 if job.status == mgr.FINISHED else float((job.percent or "0").rstrip("%"))
        except ValueError:
            percent = 0
        pb.setValue(round(percent))

    def update_summary(self):
        counts = self.manager.counts()
        text = "   ".join(f"{s}: {n}" for s, n in counts.items())
        self.statusBar.showMessage(text or "Ready.")

    def notify_done(self, event, job):
        if not self.config["general"]["notifications"]:
            return
        if event == "finished":
            title, text, icon = "Download finished", job.display_title, QtWidgets.QSystemTrayIcon.MessageIcon.Information
        else:
            title, text, icon = "Download failed", f"{job.display_title}\n{job.error}", QtWidgets.QSystemTrayIcon.MessageIcon.Warning
        if not self.isActiveWindow() and QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
            self.tray.showMessage(title, text, icon, 5000)
        else:
            QtWidgets.QApplication.alert(self)

    def choose_playlist_items(self, job):
        dialog = PlaylistDialog(job, self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.manager.choose_entries(job, dialog.selected())
        else:
            self.manager.choose_entries(job, None)

    # ---- list actions ----------------------------------------------------------------

    def job_for(self, item):
        return self.manager.jobs.get(item.data(0, ItemRoles.IdRole)) if item else None

    def selected_jobs(self):
        return [j for j in (self.job_for(i) for i in self.tw.selectedItems()) if j]

    def show_selected_details(self):
        jobs = self.selected_jobs()
        if len(jobs) != 1:
            return
        job = jobs[0]
        if job.status == mgr.ERROR:
            self.statusBar.showMessage(f"Error: {job.error}")
        elif job.filepath:
            self.statusBar.showMessage(job.filepath)
        else:
            self.statusBar.showMessage(job.url)

    def open_menu(self, position):
        item = self.tw.itemAt(position)
        job = self.job_for(item)
        if job is None:
            return
        jobs = self.selected_jobs() or [job]
        menu = QtWidgets.QMenu(self)
        open_file = menu.addAction(qta.icon("mdi6.play-circle-outline"), "Open")
        open_file.setEnabled(bool(job.filepath))
        show_folder = menu.addAction(qta.icon("mdi6.folder-open"), "Show in Folder")
        menu.addSeparator()
        pause = resume = retry = None
        if any(j.active or j.status == mgr.WAITING for j in jobs):
            pause = menu.addAction(qta.icon("mdi6.pause"), "Pause")
        if any(j.status in (mgr.PAUSED, mgr.QUEUED) for j in jobs):
            resume = menu.addAction(qta.icon("mdi6.play"), "Start / Resume")
        if any(j.status in (mgr.ERROR, mgr.FINISHED) for j in jobs):
            retry = menu.addAction(qta.icon("mdi6.refresh"),
                                   "Retry" if job.status == mgr.ERROR else "Download Again")
        edit = menu.addAction(qta.icon("mdi6.tune"), "Edit Quality…")
        edit.setEnabled(not job.active)
        choose = None
        if job.is_playlist:
            choose = menu.addAction(qta.icon("mdi6.playlist-check"), "Choose Playlist Items…")
            choose.setEnabled(job.status in (mgr.QUEUED, mgr.WAITING, mgr.PAUSED))
        menu.addSeparator()
        log = menu.addAction(qta.icon("mdi6.text-box-outline"), "Show Log…")
        copy_url = menu.addAction(qta.icon("mdi6.content-copy"), "Copy URL")
        delete = menu.addAction(qta.icon("mdi6.trash-can"), "Remove")

        action = menu.exec(self.tw.viewport().mapToGlobal(position))
        if action is None:
            return
        if action == open_file:
            self.open_file(job)
        elif action == show_folder:
            reveal_path(job.filepath if job.filepath and Path(job.filepath).exists() else job.path)
        elif action == pause:
            for j in jobs:
                self.manager.pause(j)
        elif action == resume:
            self.manager.start([j for j in jobs if j.status in (mgr.PAUSED, mgr.QUEUED)])
        elif action == retry:
            self.retry(jobs)
        elif action == edit:
            self.edit_job(job)
        elif action == choose:
            self.choose_playlist_items(job)
        elif action == log:
            self.show_log(job)
        elif action == copy_url:
            QtWidgets.QApplication.clipboard().setText("\n".join(j.url for j in jobs))
        elif action == delete:
            for j in jobs:
                self.manager.remove(j)

    def open_file(self, job):
        if job is None:
            return
        target = job.filepath
        if job.is_playlist and target:
            target = str(Path(target).parent)
        if target and Path(target).exists():
            open_path(target)
        else:
            self.statusBar.showMessage("No downloaded file yet.", 5000)

    def open_selected(self):
        jobs = self.selected_jobs()
        if jobs:
            self.open_file(jobs[0])

    def remove_selected(self):
        for job in self.selected_jobs():
            self.manager.remove(job)

    def toggle_pause_selected(self):
        for job in self.selected_jobs():
            if job.active or job.status == mgr.WAITING:
                self.manager.pause(job)
            elif job.status in (mgr.PAUSED, mgr.QUEUED):
                self.manager.resume(job)

    def retry(self, jobs):
        for job in jobs:
            if job.status == mgr.FINISHED:
                job.status = mgr.QUEUED
        self.manager.start([j for j in jobs if j.status in mgr.STARTABLE])

    def edit_job(self, job):
        dialog = EditDialog(job, self.config, self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            preset, options = dialog.values()
            if not self.manager.set_options(job, preset, options):
                self.statusBar.showMessage("Pause the download first to change it.", 5000)

    def show_log(self, job):
        dialog = self.log_dialogs.get(job.id)
        if dialog is None:
            dialog = LogDialog(job, self)
            dialog.finished.connect(lambda _=None, i=job.id: self.log_dialogs.pop(i, None))
            self.log_dialogs[job.id] = dialog
        dialog.show()
        dialog.raise_()

    def clear_all(self):
        if any(j.active for j in self.manager.jobs.values()):
            answer = QtWidgets.QMessageBox.question(
                self, "Clear all", "Some downloads are running. Stop and remove them?")
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        for job in list(self.manager.jobs.values()):
            self.manager.remove(job)

    # ---- buttons -------------------------------------------------------------------

    def button_path(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select a folder",
            self.le_path.text() or QtCore.QDir.homePath(),
            QtWidgets.QFileDialog.Option.ShowDirsOnly,
        )
        if path:
            self.le_path.setText(path)

    def button_add(self):
        links = [l.strip() for l in self.te_link.toPlainText().splitlines() if l.strip()]
        path = self.le_path.text()
        missing = [name for name, value in (("Video URL", links), ("Save to", path)) if not value]
        if missing:
            QtWidgets.QMessageBox.information(
                self,
                "Application Message",
                f"Required field{'s' if len(missing) > 1 else ''} ({', '.join(missing)}) missing.",
            )
            return False

        self.te_link.clear()
        preset = self.dd_preset.currentText()
        options = self.current_options()
        for link in links:
            self.manager.add(link, path, preset, options)
        return True

    def button_download(self):
        if self.te_link.toPlainText().strip() and not self.button_add():
            return
        if not any(j.status == mgr.QUEUED for j in self.manager.jobs.values()):
            QtWidgets.QMessageBox.information(
                self, "Application Message", "There is nothing queued to download.")
            return
        self.manager.start()

    # ---- settings / misc -------------------------------------------------------------

    def show_settings(self):
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.config["general"].update(dialog.values())
            self.save_config()
            self.apply_theme()
            self.manager._schedule()  # the parallel limit may have grown

    def open_folder(self, path):
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

    def show_about(self):
        QtWidgets.QMessageBox.about(
            self,
            "About yt-dlp-gui",
            f'<a href="https://github.com/dsymbol/yt-dlp-gui">yt-dlp-gui</a> {__version__}<br><br>'
            "A GUI for yt-dlp written in PySide6.<br>"
            "A terminal version is included: <code>yt-dlp-tui</code>.",
        )

    def add_links(self, links):
        current = self.te_link.toPlainText()
        known = set(current.split()) | {j.url for j in self.manager.jobs.values()}
        new = [l for l in links if l not in known]
        if new:
            self.te_link.setPlainText("\n".join(filter(None, [current.rstrip("\n"), *new])))
        return new

    def on_clipboard(self):
        if not self.config["general"]["clipboard_watch"]:
            return
        new = self.add_links(find_urls(QtWidgets.QApplication.clipboard().text()))
        if new:
            self.statusBar.showMessage(f"Added {len(new)} link(s) from the clipboard.", 5000)

    def dragEnterEvent(self, event):
        mime = event.mimeData()
        if mime.hasUrls() or mime.hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        mime = event.mimeData()
        links = [u.toString() for u in mime.urls() if u.scheme() in ("http", "https")]
        if not links and mime.hasText():
            links = find_urls(mime.text())
        if self.add_links(links):
            event.acceptProposedAction()

    def save_config(self):
        g = self.config["general"]
        g["current_preset"] = self.dd_preset.currentIndex()
        g["path"] = self.le_path.text()
        g.update(self.current_options())
        save_toml(CONFIG_PATH, self.config)

    def closeEvent(self, event):
        # running downloads are paused; they resume on the next start
        self.manager.shutdown()
        self.save_config()
        self.tray.hide()
        event.accept()


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    sys.exit(app.exec())
