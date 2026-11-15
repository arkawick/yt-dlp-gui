"""Terminal user interface for yt-dlp, sharing config.toml, presets and history with the GUI.

Usage:
    python tui.py [URL ...]                    interactive
    python tui.py --batch links.txt [opts]     download a list without the UI ("-" = stdin)
    python tui.py --no-ui URL ... [opts]       download URLs without the UI
"""

import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    DataTable,
    DirectoryTree,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Select,
    SelectionList,
    Static,
    Switch,
    TextArea,
)

import manager as mgr
from core import (
    BITRATES,
    CODECS,
    CONFIG_PATH,
    COOKIE_BROWSERS,
    FILENAMES,
    FRAMERATES,
    OPTION_DEFAULTS,
    RESOLUTIONS,
    ROOT,
    default_options,
    find_urls,
    format_duration,
    load_config,
    max_quality,
    open_path,
    read_clipboard,
    reveal_path,
    save_toml,
    supported_options,
)
from deps import ensure_dependencies

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s (%(module)s:%(lineno)d) %(message)s",
    # file only: writing to the terminal would corrupt the TUI
    handlers=[logging.FileHandler(ROOT / "debug.log", encoding="utf-8", delay=True)],
)
logger = logging.getLogger(__name__)

BAR_WIDTH = 16
STATUS_STYLES = {
    mgr.QUEUED: "dim",
    mgr.WAITING: "yellow",
    mgr.STARTING: "yellow",
    mgr.DOWNLOADING: "cyan",
    mgr.CONVERTING: "magenta",
    mgr.PAUSED: "dim yellow",
    mgr.FINISHED: "bold green",
    mgr.ERROR: "bold red",
}
SELECT_OPTIONS = {
    "resolution": RESOLUTIONS,
    "fps": FRAMERATES,
    "codec": CODECS,
    "bitrate": BITRATES,
}
CHECK_OPTIONS = {
    "subtitles": ("Subtitles", "Download and embed subtitles (video)"),
    "sponsorblock": ("SponsorBlock", "Cut sponsor segments; for music also non-music parts"),
    "save_cover": ("Save cover file", "Keep the thumbnail as a separate .jpg"),
    "normalize": ("Normalize volume", "Even out loudness (mp3 / re-encoded audio)"),
    "clean_titles": ("Clean music titles", 'Split "Artist - Title", drop "(Official Video)" etc.'),
}


def progress_bar(percent, status=""):
    if status == mgr.FINISHED:
        percent = "100%"
    try:
        value = max(0.0, min(100.0, float(percent.strip().rstrip("%"))))
    except (ValueError, AttributeError):
        return Text("")
    filled = round(BAR_WIDTH * value / 100)
    bar = Text("█" * filled, style="green")
    bar.append("░" * (BAR_WIDTH - filled), style="dim")
    bar.append(f" {value:5.1f}%")
    return bar


def status_text(job):
    text = job.status
    if job.awaiting_choice:
        text = "Choose items"
    elif job.status == mgr.DOWNLOADING and job.count and job.count > 1:
        text += f" {job.item}/{job.count}"
    elif job.status == mgr.FINISHED and job.count and job.count > 1:
        text += f" ({job.count})"
    return Text(text, style=STATUS_STYLES.get(job.status, ""))


def title_text(job):
    title = job.display_title
    if job.is_playlist:
        total = len(job.info["entries"])
        chosen = len(job.playlist_items.split(",")) if job.playlist_items else total
        title += f"  [{chosen}/{total}]" if chosen != total else f"  [{total} items]"
    elif job.info and job.info.get("duration"):
        title += f"  ({format_duration(job.info['duration'])})"
    return Text(title, overflow="ellipsis", no_wrap=True)


# ---- modal screens ---------------------------------------------------------------

MODAL_CSS = """
.modal { width: 80%; max-width: 110; height: auto; max-height: 90%;
         border: thick $accent; background: $surface; padding: 1 2; }
.modal .buttons { height: auto; align-horizontal: right; margin-top: 1; }
.modal .buttons Button { margin-left: 1; }
.modal .field { height: auto; }
.modal .field Label { width: 22; padding: 1 1 0 0; }
.modal .field Select, .modal .field Input { width: 1fr; }
.modal .field Switch { margin: 0; }
.modal.tall { height: 90%; }
.modal.tall .body { height: 1fr; }
"""


class FolderPicker(ModalScreen[Optional[str]]):
    """Browse for a download directory."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]
    DEFAULT_CSS = MODAL_CSS + """
    FolderPicker { align: center middle; }
    FolderPicker .modal { height: 80%; }
    FolderPicker DirectoryTree { height: 1fr; }
    #picker-selected { height: 1; margin: 1 0 0 0; color: $text-muted; }
    """

    def __init__(self, start):
        super().__init__()
        start = Path(start).expanduser() if start else Path.home()
        self.start = start if start.is_dir() else Path.home()
        self.selected = self.start

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Label("[b]Select a folder[/b]  (Enter opens, highlight to choose)")
            yield DirectoryTree(self.start)
            yield Static(str(self.selected), id="picker-selected")
            with Horizontal(classes="buttons"):
                yield Button("Up", id="up")
                yield Button("Cancel", id="cancel")
                yield Button("Select", id="ok", variant="primary")

    def on_mount(self):
        tree = self.query_one(DirectoryTree)
        tree.filter_paths = lambda paths: [p for p in paths if p.is_dir()]
        tree.focus()

    def _select(self, path):
        self.selected = Path(path)
        self.query_one("#picker-selected", Static).update(str(self.selected))

    @on(DirectoryTree.NodeHighlighted)
    def highlighted(self, event):
        if event.node.data is not None:
            self._select(event.node.data.path)

    @on(Button.Pressed, "#up")
    def go_up(self):
        tree = self.query_one(DirectoryTree)
        tree.path = Path(tree.path).resolve().parent
        self._select(tree.path)

    @on(Button.Pressed, "#cancel")
    def cancel(self):
        self.dismiss(None)

    @on(Button.Pressed, "#ok")
    def ok(self):
        self.dismiss(str(self.selected))


class PlaylistScreen(ModalScreen[Optional[list]]):
    """Choose which playlist entries to download. Dismisses with indices, or None for all."""

    BINDINGS = [Binding("escape", "dismiss(None)", "All")]
    DEFAULT_CSS = MODAL_CSS + """
    PlaylistScreen { align: center middle; }
    """

    def __init__(self, job):
        super().__init__()
        self.job = job

    def compose(self) -> ComposeResult:
        entries = self.job.info["entries"]
        with Vertical(classes="modal tall"):
            yield Label(f"[b]{self.job.info['title'] or 'Playlist'}[/b] — {len(entries)} items. "
                        "Space toggles, Esc downloads all.")
            yield SelectionList[int](*[
                (f"{e['index']:>3}. {e['title']}  {format_duration(e['duration'])}", e["index"], True)
                for e in entries
            ], classes="body")
            with Horizontal(classes="buttons"):
                yield Button("All", id="all")
                yield Button("None", id="none")
                yield Button("Download selected", id="ok", variant="primary")

    def on_mount(self):
        self.query_one(SelectionList).focus()

    @on(Button.Pressed, "#all")
    def select_all(self):
        self.query_one(SelectionList).select_all()

    @on(Button.Pressed, "#none")
    def select_none(self):
        self.query_one(SelectionList).deselect_all()

    @on(Button.Pressed, "#ok")
    def ok(self):
        selected = self.query_one(SelectionList).selected
        if not selected:
            self.notify("Select at least one item.", severity="warning")
            return
        self.dismiss(list(selected))


class EditScreen(ModalScreen[Optional[dict]]):
    """Change a queued / paused / failed download's preset and quality."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]
    DEFAULT_CSS = MODAL_CSS + "EditScreen { align: center middle; }"

    def __init__(self, job, config):
        super().__init__()
        self.job, self.config = job, config

    def compose(self) -> ComposeResult:
        job = self.job
        formats = (job.info or {}).get("formats") or []
        heights = sorted({h for h, _ in formats}, reverse=True)
        if heights:  # only what this video really has
            resolutions = {"best": "Best", **{str(h): f"{h}p" for h in heights}}
        else:
            resolutions = dict(RESOLUTIONS)
        if job.options["resolution"] not in resolutions:
            current = job.options["resolution"]
            resolutions[current] = RESOLUTIONS.get(current, current)
        with Vertical(classes="modal tall"):
            yield Label(f"[b]{job.display_title}[/b]")
            if formats:
                yield Label(f"Available: up to {max_quality(formats)}")
            with VerticalScroll(classes="body"):
                with Horizontal(classes="field"):
                    yield Label("Preset")
                    presets = list(self.config["presets"].keys())
                    yield Select([(p, p) for p in presets], value=job.preset,
                                 allow_blank=False, id="preset")
                for key, choices in (("resolution", resolutions), ("fps", FRAMERATES),
                                     ("codec", CODECS), ("bitrate", BITRATES)):
                    with Horizontal(classes="field"):
                        yield Label(key.capitalize() if key != "fps" else "FPS")
                        yield Select([(label, value) for value, label in choices.items()],
                                     value=job.options[key], allow_blank=False, id=key)
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", id="ok", variant="primary")

    def on_mount(self):
        self.refresh_enabled()

    @on(Select.Changed, "#preset")
    def refresh_enabled(self):
        supported = supported_options(self.config, self.query_one("#preset", Select).value)
        for key in ("resolution", "fps", "codec", "bitrate"):
            self.query_one(f"#{key}", Select).disabled = not supported[key]

    @on(Button.Pressed, "#cancel")
    def cancel(self):
        self.dismiss(None)

    @on(Button.Pressed, "#ok")
    def ok(self):
        result = {"preset": self.query_one("#preset", Select).value}
        for key in ("resolution", "fps", "codec", "bitrate"):
            result[key] = self.query_one(f"#{key}", Select).value
        self.dismiss(result)


class SettingsScreen(ModalScreen[Optional[dict]]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]
    DEFAULT_CSS = MODAL_CSS + """
    SettingsScreen { align: center middle; }
    """

    def __init__(self, config):
        super().__init__()
        self.general = config["general"]

    def compose(self) -> ComposeResult:
        g = self.general
        names = [(label, key) for key, (label, _) in FILENAMES.items()]
        global_args = g["global_args"] if isinstance(g["global_args"], str) else " ".join(g["global_args"])
        with Vertical(classes="modal tall"):
            yield Label("[b]Settings[/b]")
            with VerticalScroll(classes="body"):
                yield from self.field("Parallel downloads", Select(
                    [(str(n), n) for n in range(1, 11)], value=g["max_downloads"],
                    allow_blank=False, id="max_downloads"))
                yield from self.field("Video file name", Select(
                    names, value=g["filename_video"], allow_blank=False, id="filename_video"))
                yield from self.field("Music file name", Select(
                    names, value=g["filename_audio"], allow_blank=False, id="filename_audio"))
                yield from self.field("Playlist subfolder", Switch(g["playlist_folder"], id="playlist_folder"))
                yield from self.field("Subtitle languages", Input(
                    g["subtitle_langs"], id="subtitle_langs", placeholder="en.*,de"))
                yield from self.field("Browser cookies", Select(
                    [(label, key) for key, label in COOKIE_BROWSERS.items()],
                    value=g["cookies_browser"], allow_blank=False, id="cookies_browser"))
                yield from self.field("Watch clipboard", Switch(g["clipboard_watch"], id="clipboard_watch"))
                yield from self.field("Notifications", Switch(g["notifications"], id="notifications"))
                yield from self.field("Update yt-dlp on start", Switch(g["update_ytdlp"], id="update_ytdlp"))
                yield from self.field("Extra yt-dlp args", Input(
                    global_args, id="global_args", placeholder="--limit-rate 5M"))
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", id="ok", variant="primary")

    def field(self, label, widget):
        with Horizontal(classes="field"):
            yield Label(label)
            yield widget

    @on(Button.Pressed, "#cancel")
    def cancel(self):
        self.dismiss(None)

    @on(Button.Pressed, "#ok")
    def ok(self):
        result = {}
        for key in ("max_downloads", "filename_video", "filename_audio", "cookies_browser"):
            result[key] = self.query_one(f"#{key}", Select).value
        for key in ("playlist_folder", "clipboard_watch", "notifications", "update_ytdlp"):
            result[key] = self.query_one(f"#{key}", Switch).value
        for key in ("subtitle_langs", "global_args"):
            result[key] = self.query_one(f"#{key}", Input).value.strip()
        self.dismiss(result)


# ---- main app --------------------------------------------------------------------


class DownloadTable(DataTable):
    BINDINGS = [
        Binding("enter", "app.open_file", "Open"),
        Binding("o", "app.show_folder", "Folder"),
        Binding("space", "app.toggle_pause", "Pause/Resume"),
        Binding("r", "app.retry", "Retry"),
        Binding("e", "app.edit", "Edit quality"),
        Binding("c", "app.copy_url", "Copy URL"),
        Binding("delete", "app.remove", "Remove"),
    ]


class YtDlpTUI(App):
    TITLE = "yt-dlp-tui"
    SUB_TITLE = "a terminal interface for yt-dlp"

    CSS = """
    #links { height: 5; border: round $primary; }
    #links:focus { border: round $accent; }
    .row { height: auto; }
    .row Label { padding: 1 1 0 1; }
    .row Select { width: 1fr; min-width: 12; }
    #preset { max-width: 22; }
    #path { width: 1fr; }
    .row Button { margin-left: 1; min-width: 8; }
    Collapsible { padding: 0; margin: 0; border: none; }
    #extras { height: auto; }
    #extras Checkbox { margin-right: 1; }
    #downloads { height: 1fr; border: round $primary; }
    #downloads:focus { border: round $accent; }
    #log { height: 12; border: round $secondary; display: none; }
    #log.visible { display: block; }
    #status { height: 1; padding: 0 1; color: $text-muted; }
    """

    BINDINGS = [
        Binding("ctrl+n", "add", "Add", priority=True),
        Binding("ctrl+d", "download", "Download", priority=True),
        Binding("ctrl+o", "browse", "Browse", priority=True),
        Binding("ctrl+s", "settings", "Settings", priority=True),
        Binding("ctrl+g", "toggle_log", "Log", priority=True),
        Binding("ctrl+l", "clear_done", "Clear done", priority=True),
        Binding("ctrl+q", "quit", "Quit", priority=True),
    ]

    def __init__(self, initial_links=()):
        super().__init__()
        self.config = load_config()
        self.presets = list(self.config["presets"].keys())
        self.initial_links = initial_links
        self.manager = mgr.DownloadManager(self.config, self.on_manager_event)
        self.deps_ready = False
        self.ui_thread = None
        self.last_clipboard = None
        self.log_job_id = None
        self.log_lines = 0

    # ---- layout ----------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        g = self.config["general"]
        yield Header()
        links = TextArea(id="links", soft_wrap=False)
        links.border_title = "Video / playlist URLs (one per line)"
        yield links
        current = g["current_preset"] if 0 <= g["current_preset"] < len(self.presets) else 0
        with Horizontal(classes="row"):
            yield Label("Preset")
            yield Select([(p, p) for p in self.presets], value=self.presets[current],
                         allow_blank=False, id="preset")
            for key, label in (("resolution", "Res"), ("fps", "FPS"), ("codec", "Codec"),
                               ("bitrate", "Bitrate")):
                yield Label(label)
                yield Select([(text, value) for value, text in SELECT_OPTIONS[key].items()],
                             value=g[key], allow_blank=False, id=key)
        with Collapsible(title="More options", collapsed=True):
            with Horizontal(id="extras"):
                for key, (label, tip) in CHECK_OPTIONS.items():
                    yield Checkbox(label, g[key], id=key, tooltip=tip)
        with Horizontal(classes="row"):
            yield Label("Save to")
            yield Input(value=g["path"], placeholder="download folder", id="path")
            yield Button("…", id="browse", tooltip="Browse (ctrl+o)")
            yield Button("Add", id="add", tooltip="Add to list (ctrl+n)")
            yield Button("Download", id="download", variant="success", disabled=True,
                         tooltip="Start everything queued (ctrl+d)")
            yield Button("Clear done", id="clear", tooltip="Remove finished / failed rows (ctrl+l)")
        table = DownloadTable(id="downloads", cursor_type="row", zebra_stripes=True)
        table.border_title = "Downloads"
        yield table
        log = RichLog(id="log", max_lines=2000, wrap=False, markup=False, highlight=False)
        log.border_title = "yt-dlp output (ctrl+g)"
        yield log
        yield Static("Checking dependencies…", id="status")
        yield Footer()

    def on_mount(self):
        self.ui_thread = threading.get_ident()
        if self.config["general"]["tui_theme"] in self.available_themes:
            self.theme = self.config["general"]["tui_theme"]
        table = self.query_one(DataTable)
        table.add_column("Title", key="title", width=42)
        table.add_column("Format", key="format", width=22)
        table.add_column("Size", key="size", width=10)
        table.add_column("Progress", key="progress", width=BAR_WIDTH + 7)
        table.add_column("Status", key="status", width=14)
        table.add_column("Speed", key="speed", width=11)
        table.add_column("ETA", key="eta", width=8)

        links = self.query_one("#links", TextArea)
        if self.initial_links:
            links.text = "\n".join(self.initial_links)
        links.focus()
        self.refresh_option_state()
        self.manager.load_history()
        self.set_interval(1.0, self.poll_clipboard)
        self.set_interval(0.5, self.refresh_log)
        self.check_dependencies()

    # ---- dependencies -------------------------------------------------------------

    @work(thread=True, exclusive=True, group="deps")
    def check_dependencies(self):
        try:
            ensure_dependencies(
                self.config["general"]["update_ytdlp"],
                lambda msg: self.call_from_thread(self.set_status, msg),
            )
        except Exception as e:
            logger.error("Dependency check failed.", exc_info=True)
            self.call_from_thread(self.set_status, f"[red]Dependency check failed: {e}[/red]")
        self.call_from_thread(self.on_deps_ready)

    def on_deps_ready(self):
        self.deps_ready = True
        self.query_one("#download", Button).disabled = False
        self.update_summary()

    def set_status(self, message):
        self.query_one("#status", Static).update(message)

    # ---- manager events --------------------------------------------------------------

    def on_manager_event(self, event, job):
        """Called by the manager from any thread."""
        if threading.get_ident() == self.ui_thread:
            self.handle_event(event, job)
        else:
            try:
                self.call_from_thread(self.handle_event, event, job)
            except RuntimeError:
                pass  # app is shutting down

    def handle_event(self, event, job):
        table = self.query_one(DataTable)
        key = str(job.id)
        if event == "added":
            table.add_row(*self.row_cells(job), key=key)
        elif event == "updated":
            if key in table.rows:
                for column, value in zip(("title", "format", "size", "progress", "status",
                                          "speed", "eta"), self.row_cells(job)):
                    table.update_cell(key, column, value)
                if self.selected_job() is job:
                    self.show_details(job)
        elif event == "removed":
            if key in table.rows:
                table.remove_row(key)
        elif event == "playlist":
            self.push_screen(PlaylistScreen(job),
                             lambda indices: self.manager.choose_entries(job, indices))
        elif event in ("finished", "failed"):
            if self.config["general"]["notifications"]:
                if event == "finished":
                    self.notify(f"Finished: {job.display_title}", timeout=4)
                else:
                    self.notify(f"Failed: {job.display_title}\n{job.error}", severity="error", timeout=8)
                self.bell()
        self.update_summary()

    def row_cells(self, job):
        return (
            title_text(job),
            Text(self.manager.label(job), overflow="ellipsis", no_wrap=True),
            job.size or "-",
            progress_bar(job.percent, job.status),
            status_text(job),
            job.speed or "-",
            job.eta or "-",
        )

    def update_summary(self):
        if not self.deps_ready:
            return
        counts = self.manager.counts()
        self.set_status("  ".join(f"{s}: {n}" for s, n in counts.items()) or "Ready.")

    # ---- options -------------------------------------------------------------------

    def current_options(self):
        options = {}
        for key in OPTION_DEFAULTS:
            if key in SELECT_OPTIONS:
                options[key] = self.query_one(f"#{key}", Select).value
            else:
                options[key] = self.query_one(f"#{key}", Checkbox).value
        return options

    @on(Select.Changed, "#preset")
    def refresh_option_state(self):
        supported = supported_options(self.config, self.query_one("#preset", Select).value)
        for key in OPTION_DEFAULTS:
            self.query_one(f"#{key}").disabled = not supported[key]

    # ---- actions -------------------------------------------------------------------

    @on(Button.Pressed, "#browse")
    def action_browse(self):
        def chosen(path):
            if path:
                self.query_one("#path", Input).value = path

        self.push_screen(FolderPicker(self.query_one("#path", Input).value), chosen)

    @on(Button.Pressed, "#add")
    def action_add(self):
        links_area = self.query_one("#links", TextArea)
        links = [l.strip() for l in links_area.text.splitlines() if l.strip()]
        path = os.path.expanduser(self.query_one("#path", Input).value.strip())
        missing = [name for name, value in (("URL", links), ("Save to", path)) if not value]
        if missing:
            self.notify(f"Required: {', '.join(missing)}", severity="warning")
            return False

        links_area.clear()
        preset = self.query_one("#preset", Select).value
        options = self.current_options()
        for link in links:
            self.manager.add(link, path, preset, options)
        return True

    @on(Button.Pressed, "#download")
    def action_download(self):
        if not self.deps_ready:
            self.notify("Still checking dependencies, please wait…", severity="warning")
            return
        if self.query_one("#links", TextArea).text.strip() and not self.action_add():
            return
        if not any(j.status == mgr.QUEUED for j in self.manager.jobs.values()):
            self.notify("Nothing queued. Add some URLs first.", severity="warning")
            return
        self.manager.start()

    @on(Button.Pressed, "#clear")
    def action_clear_done(self):
        self.manager.clear()

    def selected_job(self):
        table = self.query_one(DataTable)
        if not table.row_count:
            return None
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        return self.manager.jobs.get(int(row_key.value))

    @on(DataTable.RowHighlighted)
    def row_highlighted(self, event):
        if job := self.selected_job():
            self.show_details(job)
            if self.log_visible():
                self.refresh_log()

    def show_details(self, job):
        if job.status == mgr.ERROR:
            self.set_status(f"[red]Error:[/red] {job.error}")
        elif job.probe_error and not job.info:
            self.set_status(f"[yellow]Could not fetch info:[/yellow] {job.probe_error}")
        elif job.filepath:
            self.set_status(f"File: {job.filepath}")
        else:
            self.set_status(job.url)

    def action_remove(self):
        if job := self.selected_job():
            self.manager.remove(job)

    def action_toggle_pause(self):
        if not (job := self.selected_job()):
            return
        if job.active or job.status == mgr.WAITING:
            self.manager.pause(job)
        elif job.status in (mgr.PAUSED, mgr.QUEUED):
            self.manager.resume(job)

    def action_retry(self):
        if (job := self.selected_job()) and job.status in (mgr.ERROR, mgr.PAUSED, mgr.FINISHED):
            if job.status == mgr.FINISHED:
                job.status = mgr.QUEUED
            self.manager.start([job])

    def action_edit(self):
        if not (job := self.selected_job()):
            return
        if job.active:
            self.notify("Pause the download first to change its quality.", severity="warning")
            return

        def done(result):
            if result:
                preset = result.pop("preset")
                self.manager.set_options(job, preset, result)

        self.push_screen(EditScreen(job, self.config), done)

    def action_open_file(self):
        if not (job := self.selected_job()):
            return
        target = job.filepath
        if job.is_playlist and target:
            target = str(Path(target).parent)
        if not target or not Path(target).exists():
            self.notify("No downloaded file yet.", severity="warning")
            return
        try:
            open_path(target)
        except OSError as e:
            self.notify(f"Could not open: {e}", severity="error")

    def action_show_folder(self):
        if job := self.selected_job():
            try:
                reveal_path(job.filepath if job.filepath and Path(job.filepath).exists() else job.path)
            except OSError as e:
                self.notify(f"Could not open folder: {e}", severity="error")

    def action_copy_url(self):
        if job := self.selected_job():
            self.copy_to_clipboard(job.url)
            self.notify("Copied URL to clipboard.")

    def action_settings(self):
        def done(result):
            if not result:
                return
            self.config["general"].update(result)
            self.save_config()
            self.manager._schedule()  # the parallel limit may have grown
            self.notify("Settings saved.")

        self.push_screen(SettingsScreen(self.config), done)

    # ---- log panel -----------------------------------------------------------------

    def log_visible(self):
        return self.query_one("#log", RichLog).has_class("visible")

    def action_toggle_log(self):
        self.query_one("#log", RichLog).toggle_class("visible")
        self.log_job_id, self.log_lines = None, 0
        self.refresh_log()

    def refresh_log(self):
        if not self.log_visible() or (job := self.selected_job()) is None:
            return
        log = self.query_one("#log", RichLog)
        lines = list(job.log)
        if job.id != self.log_job_id or len(lines) < self.log_lines:
            log.clear()
            self.log_job_id, self.log_lines = job.id, 0
            log.border_title = f"yt-dlp output: {job.display_title} (ctrl+g)"
            if not lines:
                log.write("(no output yet)")
        for line in lines[self.log_lines:]:
            log.write(line)
        self.log_lines = len(lines)

    # ---- clipboard -----------------------------------------------------------------

    def poll_clipboard(self):
        if self.config["general"]["clipboard_watch"]:
            self.read_clipboard_worker()
        else:
            self.last_clipboard = None

    @work(thread=True, exclusive=True, group="clipboard")
    def read_clipboard_worker(self):
        text = read_clipboard()
        self.call_from_thread(self.on_clipboard, text)

    def on_clipboard(self, text):
        if self.last_clipboard is None:  # don't grab what was copied before watching
            self.last_clipboard = text
            return
        if text == self.last_clipboard:
            return
        self.last_clipboard = text
        links_area = self.query_one("#links", TextArea)
        known = set(links_area.text.split()) | {j.url for j in self.manager.jobs.values()}
        new = [u for u in find_urls(text) if u not in known]
        if new:
            existing = links_area.text.rstrip("\n")
            links_area.text = "\n".join(filter(None, [existing, *new]))
            self.notify(f"Added {len(new)} link(s) from the clipboard.")

    # ---- quit ----------------------------------------------------------------------

    def save_config(self):
        g = self.config["general"]
        g["current_preset"] = self.presets.index(self.query_one("#preset", Select).value)
        g["path"] = self.query_one("#path", Input).value
        g.update(self.current_options())
        g["tui_theme"] = self.theme
        save_toml(CONFIG_PATH, self.config)

    async def action_quit(self):
        # running downloads are paused; they resume on the next start
        self.manager.shutdown()
        self.save_config()
        self.exit()


# ---- batch mode --------------------------------------------------------------------


def run_batch(args, urls):
    """Download urls with the saved settings, printing progress to the console."""
    config = load_config()
    g = config["general"]
    presets = list(config["presets"].keys())
    preset = args.preset or presets[g["current_preset"] if 0 <= g["current_preset"] < len(presets) else 0]
    if preset not in config["presets"]:
        print(f"Unknown preset {preset!r}. Available: {', '.join(presets)}", file=sys.stderr)
        return 2
    path = os.path.expanduser(args.path or g["path"] or ".")
    options = default_options(config)
    for key in ("resolution", "fps", "codec", "bitrate"):
        if getattr(args, key):
            options[key] = getattr(args, key)
    if args.parallel:
        g["max_downloads"] = args.parallel

    print("Checking dependencies...")
    last = [0.0]

    def dep_progress(message):
        if time.monotonic() - last[0] > 1:
            last[0] = time.monotonic()
            print(f"  {message}")

    ensure_dependencies(g["update_ytdlp"], dep_progress)

    done = threading.Event()
    lock = threading.Lock()
    finished, failed, ours = [], [], set()
    seen_status = {}

    def on_event(event, job):
        if job.id not in ours:
            return
        with lock:
            if event == "updated" and seen_status.get(job.id) != job.status:
                seen_status[job.id] = job.status
                if job.status in (mgr.DOWNLOADING, mgr.CONVERTING):
                    print(f"[{job.id}] {job.status}: {job.display_title}")
            elif event == "finished":
                finished.append(job)
                print(f"[{job.id}] Finished: {job.display_title}"
                      + (f"  -> {job.filepath}" if job.filepath else ""))
            elif event == "failed":
                failed.append(job)
                print(f"[{job.id}] FAILED: {job.display_title}: {job.error}")
            if len(finished) + len(failed) == len(urls):
                done.set()

    history = None if args.no_history else mgr.HISTORY_PATH
    manager = mgr.DownloadManager(config, on_event, ask_playlist=False, history_path=history)
    manager.load_history()
    jobs = []
    for url in urls:
        job = manager.add(url, path, preset, options)
        ours.add(job.id)
        jobs.append(job)
    print(f"Downloading {len(urls)} link(s) as {manager.label(jobs[0])} into {path}")
    manager.start(jobs)
    try:
        while not done.wait(10):
            counts = {}
            for job in jobs:
                counts[job.status] = counts.get(job.status, 0) + 1
            print("  ... " + ", ".join(f"{s}: {n}" for s, n in counts.items()))
    except KeyboardInterrupt:
        print("Stopping (partial downloads are kept and resume next time)...")
        manager.shutdown()
        return 130
    manager.save_history()
    print(f"Done: {len(finished)} finished, {len(failed)} failed.")
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Terminal interface for yt-dlp.")
    parser.add_argument("urls", nargs="*", help="links to pre-fill (or download with --no-ui)")
    parser.add_argument("--batch", metavar="FILE",
                        help='download the links in FILE ("-" = stdin) without the UI')
    parser.add_argument("--no-ui", action="store_true", help="download the given URLs without the UI")
    parser.add_argument("--preset", help="preset name (default: last used)")
    parser.add_argument("--path", help="download folder (default: last used)")
    parser.add_argument("--resolution", choices=list(RESOLUTIONS))
    parser.add_argument("--fps", choices=list(FRAMERATES))
    parser.add_argument("--codec", choices=list(CODECS))
    parser.add_argument("--bitrate", choices=list(BITRATES))
    parser.add_argument("--parallel", type=int, choices=range(1, 11), metavar="N")
    parser.add_argument("--no-history", action="store_true",
                        help="don't record these downloads in the history")
    args = parser.parse_args(argv)

    if args.batch or args.no_ui:
        urls = list(args.urls)
        if args.batch:
            source = sys.stdin if args.batch == "-" else open(args.batch, encoding="utf-8")
            with source:
                urls += [l.strip() for l in source if l.strip() and not l.lstrip().startswith("#")]
        if not urls:
            parser.error("no URLs given")
        return run_batch(args, urls)

    YtDlpTUI(initial_links=args.urls).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
