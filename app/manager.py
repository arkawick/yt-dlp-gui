"""Download queue shared by the GUI, the TUI and batch mode.

Runs yt-dlp jobs on background threads with a parallel limit, probes links for
title / formats / playlist entries, supports pause / resume / retry, and keeps a
history in history.json. Front-ends get events through on_event(event, job), called
from any thread; they must hand them over to their UI thread themselves.

events: added, updated, removed, playlist (entries need choosing), finished, failed
"""

import json
import logging
import shutil
import subprocess as sp
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Optional

from core import (
    HISTORY_PATH,
    TEMP_PREFIX,
    build_command,
    expected_quality,
    parse_line,
    probe,
    quality_label,
    run_process,
    supported_options,
)

logger = logging.getLogger(__name__)

QUEUED = "Queued"  # added, not started yet
WAITING = "Waiting"  # started, waiting for a free slot (or a playlist choice)
STARTING = "Starting"
DOWNLOADING = "Downloading"
CONVERTING = "Converting"
PAUSED = "Paused"
FINISHED = "Finished"
ERROR = "Error"

RUNNING = (STARTING, DOWNLOADING, CONVERTING)
STARTABLE = (QUEUED, PAUSED, ERROR)
DONE = (FINISHED, ERROR)

# yt-dlp post-processors that run before the download (or just move the file):
# not shown as "Converting"
PRE_DOWNLOAD_STEPS = {"MetadataParser", "MetadataFromField", "ThumbnailsConvertor",
                      "SponsorBlock", "MoveFiles"}

HISTORY_LIMIT = 500
UPDATE_INTERVAL = 0.25  # seconds between progress events per job


@dataclass
class Job:
    id: int
    url: str
    path: str
    preset: str
    options: dict
    status: str = QUEUED
    uid: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    title: str = ""
    size: str = ""
    percent: str = ""
    speed: str = ""
    eta: str = ""
    item: Optional[int] = None
    count: Optional[int] = None
    error: str = ""
    filepath: str = ""
    playlist_items: str = ""
    chosen: bool = False  # playlist entries were picked (or the user kept them all)
    info: Optional[dict] = None
    probe_error: str = ""
    added: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    # runtime only
    log: deque = field(default_factory=lambda: deque(maxlen=2000), repr=False)
    awaiting_choice: bool = field(default=False, repr=False)
    _proc: Optional[sp.Popen] = field(default=None, repr=False)
    _stop: Optional[str] = field(default=None, repr=False)  # "pause" | "remove"
    _probed: threading.Event = field(default_factory=threading.Event, repr=False)

    PERSISTED = (
        "id", "url", "path", "preset", "options", "status", "uid", "title", "size",
        "percent", "error", "filepath", "playlist_items", "chosen", "info", "probe_error",
        "added", "finished_at", "item", "count",
    )

    @property
    def temp_name(self):
        return f"{TEMP_PREFIX}{self.uid}"

    @property
    def temp_dir(self):
        return Path(self.path) / self.temp_name

    @property
    def active(self):
        return self.status in RUNNING

    @property
    def is_playlist(self):
        return bool(self.info and self.info.get("playlist"))

    @property
    def display_title(self):
        if self.is_playlist and self.info.get("title"):
            return self.info["title"]
        return self.title or (self.info or {}).get("title") or self.url

    def to_dict(self):
        return {k: getattr(self, k) for k in self.PERSISTED}

    @classmethod
    def from_dict(cls, data):
        known = {f.name for f in fields(cls)}
        job = cls(**{k: v for k, v in data.items() if k in known and k in cls.PERSISTED})
        if job.info is not None or job.probe_error:
            job._probed.set()
        return job


class DownloadManager:
    def __init__(self, config, on_event, ask_playlist=True, history_path=HISTORY_PATH):
        self.config = config
        self.on_event = on_event
        self.ask_playlist = ask_playlist
        self.history_path = history_path
        self.jobs: dict[int, Job] = {}
        self.next_id = 0
        self.lock = threading.RLock()
        self.probe_slots = threading.Semaphore(2)
        self.closing = False

    # ---- public API (call from the UI thread) ---------------------------------------

    @property
    def max_parallel(self):
        return self.config["general"]["max_downloads"]

    def add(self, url, path, preset, options):
        with self.lock:
            job = Job(self.next_id, url, path, preset, dict(options))
            self.next_id += 1
            self.jobs[job.id] = job
        logger.info(f"Queued download ({job.id}) added {url}")
        self._emit("added", job)
        self._start_probe(job)
        return job

    def start(self, jobs=None):
        """Start the given (default: all queued) jobs, as slots become free."""
        with self.lock:
            if jobs is None:
                jobs = [j for j in self.jobs.values() if j.status == QUEUED]
            for job in jobs:
                if job.status in STARTABLE:
                    job.status = WAITING
                    job.error = ""
                    self._emit("updated", job)
        self._schedule()

    def pause(self, job):
        with self.lock:
            if job.status == WAITING:
                job.status = PAUSED
                self._emit("updated", job)
            elif job.active:
                self._kill(job, "pause")
        self.save_history()

    def resume(self, job):
        self.start([job])

    def remove(self, job):
        with self.lock:
            self.jobs.pop(job.id, None)
            if job.active:
                self._kill(job, "remove")  # the worker deletes the temp folder
            else:
                self._delete_temp(job)
        logger.debug(f"Removing download ({job.id}): {job.url}")
        self._emit("removed", job)
        self._schedule()
        self.save_history()

    def clear(self, statuses=DONE):
        """Remove every job in one of the given states."""
        for job in [j for j in self.jobs.values() if j.status in statuses]:
            self.remove(job)

    def choose_entries(self, job, indices):
        """Answer a "playlist" event: indices (1-based) to download, or None for all."""
        with self.lock:
            total = len(job.info["entries"]) if job.info else 0
            if indices is not None and len(indices) < total:
                job.playlist_items = ",".join(str(i) for i in sorted(indices))
            else:
                job.playlist_items = ""
            job.awaiting_choice = False
            job.chosen = True
            self._emit("updated", job)
        self._schedule()

    def set_options(self, job, preset=None, options=None):
        """Change a job's preset / options (only while it isn't running)."""
        with self.lock:
            if job.active:
                return False
            if preset is not None:
                job.preset = preset
            if options is not None:
                job.options.update(options)
            self._emit("updated", job)
            return True

    def shutdown(self):
        """Pause everything that is running (partial downloads are kept) and save."""
        self.closing = True
        with self.lock:
            for job in self.jobs.values():
                if job.active:
                    self._kill(job, "pause")
                elif job.status == WAITING:
                    job.status = PAUSED
        self.save_history()

    def counts(self):
        counts = {}
        for job in self.jobs.values():
            counts[job.status] = counts.get(job.status, 0) + 1
        return counts

    def label(self, job):
        """Format column text: preset and options, plus the quality the probe predicts."""
        text = quality_label(self.config, job.preset, job.options)
        formats = (job.info or {}).get("formats")
        if formats and supported_options(self.config, job.preset)["resolution"]:
            expected = expected_quality(formats, job.options)
            res = job.options["resolution"]
            if res == "best" or not expected.startswith(f"{res}p"):
                text += f" → {expected}"
        return text

    # ---- history -----------------------------------------------------------------

    def load_history(self):
        if self.history_path is None:
            return
        try:
            data = json.loads(self.history_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception:
            logger.error("Could not read history.", exc_info=True)
            return
        with self.lock:
            for record in data[-HISTORY_LIMIT:]:
                try:
                    job = Job.from_dict(record)
                except TypeError:
                    continue
                if job.status in RUNNING + (WAITING,):
                    job.status = PAUSED
                self.jobs[job.id] = job
                self.next_id = max(self.next_id, job.id + 1)
        for job in list(self.jobs.values()):
            self._emit("added", job)
            if not job._probed.is_set():
                self._start_probe(job)

    def save_history(self):
        if self.history_path is None:
            return
        with self.lock:
            data = [j.to_dict() for j in self.jobs.values()][-HISTORY_LIMIT:]
        try:
            tmp = self.history_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            tmp.replace(self.history_path)
        except Exception:
            logger.error("Could not save history.", exc_info=True)

    # ---- internals -----------------------------------------------------------------

    def _emit(self, event, job):
        try:
            self.on_event(event, job)
        except Exception:
            logger.error(f"on_event({event}) failed", exc_info=True)

    def _start_probe(self, job):
        threading.Thread(target=self._probe, args=(job,), daemon=True).start()

    def _probe(self, job):
        with self.probe_slots:
            if job.id not in self.jobs or self.closing:
                job._probed.set()
                return
            try:
                info = probe(self.config, job.url)
            except Exception as e:
                job.probe_error = str(e)
                logger.warning(f"Probe ({job.id}) failed: {e}")
                job._probed.set()
                self._emit("updated", job)
                return
        with self.lock:
            job.info = info
            if (info["playlist"] and info["entries"] and self.ask_playlist
                    and not job.chosen and job.status not in DONE):
                job.awaiting_choice = True
            job._probed.set()
        self._emit("updated", job)
        if job.awaiting_choice:
            self._emit("playlist", job)
        self._schedule()

    def _schedule(self):
        if self.closing:
            return
        with self.lock:
            running = sum(1 for j in self.jobs.values() if j.active)
            for job in self.jobs.values():
                if running >= self.max_parallel:
                    break
                if job.status == WAITING and not job.awaiting_choice:
                    job.status = STARTING
                    job._stop = None
                    running += 1
                    self._emit("updated", job)
                    threading.Thread(target=self._run, args=(job,), daemon=True).start()

    def _kill(self, job, reason):
        job._stop = reason
        proc = job._proc
        if proc and proc.poll() is None:
            try:
                if sys.platform == "win32":
                    # whole tree: yt-dlp, its python child and ffmpeg
                    sp.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, creationflags=sp.CREATE_NO_WINDOW)
                else:
                    proc.terminate()
            except Exception:
                logger.error(f"Could not stop download ({job.id})", exc_info=True)

    def _delete_temp(self, job):
        # a just-killed yt-dlp / ffmpeg can hold files open for a moment
        for _ in range(20):
            shutil.rmtree(job.temp_dir, ignore_errors=True)
            if not job.temp_dir.exists():
                return
            time.sleep(0.25)

    def _run(self, job):
        job._probed.wait(timeout=180)  # playlist detection decides the output template
        if job._stop:
            return self._finish_stopped(job)
        if job.awaiting_choice:
            # turned out to be a playlist: give the slot back until entries are chosen
            with self.lock:
                job.status = WAITING
            self._emit("updated", job)
            return self._schedule()

        command = build_command(
            self.config, job.url, job.path, job.preset, job.options,
            temp_name=job.temp_name, playlist=job.is_playlist,
            playlist_items=job.playlist_items,
        )
        logger.info(f"Download ({job.id}) starting with cmd: {command}")
        job.log.append("$ " + " ".join(command))
        last_emit = 0.0
        last_error = ""
        last_dest = ""

        def on_start(proc):
            job._proc = proc
            if job._stop:  # paused / removed while starting
                self._kill(job, job._stop)

        def on_line(line):
            nonlocal last_emit, last_error, last_dest
            job.log.append(line)
            event = parse_line(line)
            if event is None:
                return
            kind, data = event
            changed = False
            if kind == "progress":
                job.title = data["title"] or job.title
                job.size, job.percent = data["size"], data["percent"]
                job.speed, job.eta = data["speed"], data["eta"]
                job.item, job.count = data["item"], data["count"]
                changed = job.status != DOWNLOADING
                job.status = DOWNLOADING
            elif kind == "postprocess":
                name, _status = data
                if name not in PRE_DOWNLOAD_STEPS and job.status != CONVERTING:
                    job.status = CONVERTING
                    changed = True
            elif kind == "file":
                job.filepath = data
            elif kind == "dest":
                last_dest = data
            elif kind == "error":
                last_error = data
            elif kind == "warning":
                logger.warning(f"Download ({job.id}) {data}")
            now = time.monotonic()
            if changed or now - last_emit >= UPDATE_INTERVAL:
                last_emit = now
                self._emit("updated", job)

        try:
            returncode = run_process(command, on_line, on_start)
        except Exception as e:
            logger.error(f"Download ({job.id}) failed to start.", exc_info=True)
            returncode, last_error = -1, f"Could not run yt-dlp: {e}"
        job._proc = None

        if job._stop:
            return self._finish_stopped(job)

        if not job.filepath and last_dest and not last_dest.startswith(str(job.temp_dir)):
            job.filepath = last_dest
        job.finished_at = time.time()
        job.speed = job.eta = ""
        if returncode == 0:
            job.status, job.percent, job.error = FINISHED, "100%", ""
            self._delete_temp(job)
            logger.info(f"Download ({job.id}) finished.")
            self._emit("updated", job)
            self._emit("finished", job)
        else:
            job.status = ERROR
            job.error = last_error or f"yt-dlp exited with code {returncode}"
            logger.error(f"Download ({job.id}) returncode: {returncode}\n" + "\n".join(job.log))
            self._emit("updated", job)
            self._emit("failed", job)
        self.save_history()
        self._schedule()

    def _finish_stopped(self, job):
        job._proc = None
        job.speed = job.eta = ""
        if job._stop == "remove":
            self._delete_temp(job)
        else:
            job.status = PAUSED
            logger.info(f"Download ({job.id}) paused.")
            self._emit("updated", job)
        self._schedule()
