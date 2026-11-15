"""Front-end agnostic yt-dlp logic shared by the Qt GUI (app.py) and the TUI (tui.py):
settings, yt-dlp command building, output parsing, probing and small OS helpers."""

import json
import logging
import os
import re
import shlex
import subprocess as sp
import sys
from pathlib import Path

import tomlkit
from platformdirs import user_data_dir

ROOT = Path(__file__).parent
BIN_DIR = Path(user_data_dir("yt-dlp-gui"))  # user data dir for persistence
CONFIG_PATH = ROOT / "config.toml"
HISTORY_PATH = ROOT / "history.json"

TEMP_PREFIX = ".yt-dlp-gui-tmp-"
CREATE_NO_WINDOW = sp.CREATE_NO_WINDOW if sys.platform == "win32" else 0

logger = logging.getLogger(__name__)

# ---- choices (value -> label); values are what config.toml stores -------------------

RESOLUTIONS = {
    "best": "Best",
    "2160": "2160p (4K)",
    "1440": "1440p (2K)",
    "1080": "1080p",
    "720": "720p",
    "480": "480p",
    "360": "360p",
}
FRAMERATES = {"any": "Any", "60": "60 fps", "30": "30 fps"}
CODECS = {
    "any": "Any",
    "avc": "H.264 (compatible)",
    "vp9": "VP9",
    "av01": "AV1",
}
BITRATES = {
    "best": "Best (VBR ~245k)",
    "320K": "320 kbps",
    "256K": "256 kbps",
    "192K": "192 kbps",
    "128K": "128 kbps",
}
FILENAMES = {
    "title": ("Title", "%(title)s.%(ext)s"),
    "title_id": ("Title [id]", "%(title)s [%(id)s].%(ext)s"),
    "artist_title": ("Artist - Title", "%(artist,uploader)s - %(title)s.%(ext)s"),
    "date_title": ("Date - Title", "%(upload_date>%Y-%m-%d)s - %(title)s.%(ext)s"),
}
COOKIE_BROWSERS = {
    "": "None",
    "chrome": "Chrome",
    "edge": "Edge",
    "firefox": "Firefox",
    "brave": "Brave",
    "opera": "Opera",
    "vivaldi": "Vivaldi",
    "chromium": "Chromium",
    "safari": "Safari",
}
THEMES = {"system": "System", "light": "Light", "dark": "Dark"}

# per-download options, remembered as the last-used values in [general]
OPTION_DEFAULTS = {
    "resolution": "best",
    "fps": "any",
    "codec": "any",
    "bitrate": "best",
    "subtitles": False,
    "sponsorblock": False,
    "save_cover": False,
    "normalize": False,
    "clean_titles": True,
}
CHOICES = {
    "resolution": RESOLUTIONS,
    "fps": FRAMERATES,
    "codec": CODECS,
    "bitrate": BITRATES,
    "filename_video": FILENAMES,
    "filename_audio": FILENAMES,
    "cookies_browser": COOKIE_BROWSERS,
    "theme": THEMES,
}
GENERAL_DEFAULTS = {
    "current_preset": 0,
    "path": "",
    **OPTION_DEFAULTS,
    "max_downloads": 3,
    "filename_video": "title",
    "filename_audio": "artist_title",
    "playlist_folder": True,
    "subtitle_langs": "en.*",
    "cookies_browser": "",
    "clipboard_watch": False,
    "notifications": True,
    "theme": "system",
    "tui_theme": "textual-dark",
    "global_args": "",
    "update_ytdlp": True,
}

# audio formats yt-dlp re-encodes to (others are usually copied from YouTube unchanged,
# so a bitrate or volume filter would be silently ignored)
BITRATE_FORMATS = {"mp3"}
REENCODE_FORMATS = {"mp3", "wav", "flac", "alac"}

# square, centred crop of the thumbnail for audio cover art
SQUARE_COVER = (
    "ThumbnailsConvertor+FFmpeg_o:-c:v mjpeg -qmin 1 -qscale:v 1 "
    "-vf crop=\"'if(gt(ih,iw),iw,ih)':'if(gt(iw,ih),ih,iw)'\""
)
# "(Official Video)", "[Lyrics]", "(HD Remaster)"... in music titles
TITLE_JUNK = (
    r"(?i)\s*[(\[{][^)\]}]*\b(?:official|lyrics?|audio|video|visuali[sz]er|"
    r"remaster(?:ed)?|hd|hq|4k|mv|m/v)\b[^)\]}]*[)\]}]"
)
SPONSORBLOCK = "sponsor,selfpromo,interaction"


# ---- config -----------------------------------------------------------------------


def load_toml(path):
    with open(path, "r", encoding="utf-8") as file:
        return tomlkit.parse(file.read())


def save_toml(path, data):
    with open(path, "w", encoding="utf-8") as file:
        file.write(tomlkit.dumps(data))


def load_config(path=CONFIG_PATH):
    """Load config.toml, filling in (and validating) every [general] setting."""
    config = load_toml(path)
    general = config["general"]
    for key, default in GENERAL_DEFAULTS.items():
        value = general.get(key, default)
        if key in CHOICES and value not in CHOICES[key]:
            value = default
        elif isinstance(default, bool) and not isinstance(value, bool):
            value = default
        elif key == "max_downloads":
            value = value if isinstance(value, int) and 1 <= value <= 10 else default
        general[key] = value
    return config


def default_options(config):
    """The last-used per-download options."""
    return {key: config["general"][key] for key in OPTION_DEFAULTS}


# ---- presets ----------------------------------------------------------------------


def split_args(args):
    return list(args) if isinstance(args, list) else shlex.split(args)


def preset_args(config, preset):
    return split_args(config["presets"][preset])


def _arg_value(args, *flags):
    value = None
    for flag, nxt in zip(args, args[1:]):
        if flag in flags:
            value = nxt
    return value


def is_audio_preset(config, preset):
    args = preset_args(config, preset)
    return "-x" in args or "--extract-audio" in args


def audio_format(config, preset):
    """Target --audio-format of an audio preset ("best" if unspecified)."""
    return _arg_value(preset_args(config, preset), "--audio-format") or "best"


def supported_options(config, preset):
    """Which per-download options apply to a preset."""
    audio = is_audio_preset(config, preset)
    fmt = audio_format(config, preset) if audio else None
    return {
        "resolution": not audio,
        "fps": not audio,
        "codec": not audio,
        "subtitles": not audio,
        "bitrate": audio and fmt in BITRATE_FORMATS,
        "normalize": audio and fmt in REENCODE_FORMATS,
        "clean_titles": audio,
        "sponsorblock": True,
        "save_cover": True,
    }


def quality_label(config, preset, options):
    """Short description for the downloads list, e.g. "best 1080p60" or "mp3 320k"."""
    supported = supported_options(config, preset)
    parts = [preset]
    if supported["bitrate"] and options["bitrate"] != "best":
        parts.append(options["bitrate"].lower())
    if supported["resolution"]:
        quality = f"{options['resolution']}p" if options["resolution"] != "best" else ""
        if options["fps"] != "any":
            quality += options["fps"] if quality else f"{options['fps']}fps"
        if quality:
            parts.append(quality)
        if options["codec"] != "any":
            parts.append({"avc": "h264", "av01": "av1"}.get(options["codec"], options["codec"]))
    return " ".join(parts)


def format_sort(options):
    """-S fields: the codec preference first (so H.264 means H.264 even if that caps it
    at 1080p), then "at or below" resolution / fps limits that fall back to the closest
    available format."""
    fields = []
    if options.get("codec", "any") != "any":
        fields.append(f"vcodec:{options['codec']}")
    if options.get("resolution", "best") != "best":
        fields.append(f"res:{options['resolution']}")
    if options.get("fps", "any") != "any":
        fields.append(f"fps:{options['fps']}")
    return fields


# ---- command ----------------------------------------------------------------------

PROGRESS_FIELDS = (
    "progress.status",
    "progress._total_bytes_estimate_str",
    "progress._percent_str",
    "progress._speed_str",
    "progress._eta_str",
    "info.title",
    "info.playlist_autonumber",
    "info.n_entries",
)
SEP = "__SEP__"
DL_MARK = "__DL__"
PP_MARK = "__PP__"
PROGRESS_TEMPLATE = DL_MARK + SEP.join(f"%({f})s" for f in PROGRESS_FIELDS)
PP_TEMPLATE = PP_MARK + "%(progress.postprocessor)s" + SEP + "%(progress.status)s"


def build_command(config, url, path, preset, options, temp_name=None,
                  playlist=False, playlist_items=""):
    """The full yt-dlp command for one download.

    App-generated options come first so a preset or global_args can override them.
    """
    general = config["general"]
    supported = supported_options(config, preset)
    audio = is_audio_preset(config, preset)
    p_args = preset_args(config, preset)
    g_args = split_args(general["global_args"])
    user_args = p_args + g_args

    args = [
        "yt-dlp",
        "--newline",
        "--no-simulate",
        "--progress",
        "--progress-template", f"download:{PROGRESS_TEMPLATE}",
        "--progress-template", f"postprocess:{PP_TEMPLATE}",
        "-P", path,
    ]
    if temp_name and not any(a.startswith("temp:") for a in user_args):
        # a private work folder per download, so simultaneous downloads of the same video
        # (e.g. as mp4 and mp3) don't share and delete each other's intermediate files
        args += ["-P", f"temp:{temp_name}"]

    template = FILENAMES[general["filename_audio" if audio else "filename_video"]][1]
    if playlist and general["playlist_folder"]:
        template = "%(playlist_title,playlist_id)s/" + template
    args += ["-o", template]

    if general["cookies_browser"]:
        args += ["--cookies-from-browser", general["cookies_browser"]]
    if playlist_items:
        args += ["-I", playlist_items]

    sort = format_sort(options) if supported["resolution"] else []
    if sort:
        args += ["-S", ",".join(sort)]

    if supported["subtitles"] and options["subtitles"]:
        langs = general["subtitle_langs"] or "en.*"
        args += ["--write-subs", "--write-auto-subs", "--sub-langs", f"{langs},-live_chat",
                 "--embed-subs"]
    if options["sponsorblock"]:
        cats = SPONSORBLOCK + (",music_offtopic" if audio else "")
        args += ["--sponsorblock-remove", cats]
    if options["save_cover"]:
        args += ["--write-thumbnail"]

    if audio:
        if supported["clean_titles"] and options["clean_titles"]:
            args += [
                # "Artist - Title" -> artist + title, only when YouTube gave no artist
                "--parse-metadata", r"%(artist|)s|%(title)s:^\|(?P<artist>.+?) - (?P<title>.+)$",
                "--replace-in-metadata", "title,track", TITLE_JUNK, "",
            ]
        if playlist:
            # an album / playlist: album name and track numbers, unless YouTube has them
            args += [
                "--parse-metadata", "%(album,playlist_title|)s:%(album)s",
                "--parse-metadata", "%(track_number,playlist_index|)s:%(track_number)s",
            ]

    args += user_args
    # after the preset, so the chosen bitrate wins over the preset's --audio-quality
    if supported["bitrate"] and options["bitrate"] != "best":
        args += ["--audio-quality", options["bitrate"]]
    if supported["normalize"] and options["normalize"]:
        args += ["--ppa", "ExtractAudio:-af loudnorm=I=-14:TP=-1.5:LRA=11"]
    args += ["--", url]
    return args


def probe_command(config, url):
    args = ["yt-dlp", "-J", "--flat-playlist", "--no-warnings"]
    if config["general"]["cookies_browser"]:
        args += ["--cookies-from-browser", config["general"]["cookies_browser"]]
    return args + ["--", url]


# ---- output parsing ---------------------------------------------------------------

MOVED_RE = re.compile(r'^\[MoveFiles\] Moving file ".+" to "(.+)"$')
ALREADY_RE = re.compile(r"^\[download\] (.+) has already been downloaded$")
DEST_RE = re.compile(
    r'^\[(?:download|ExtractAudio|VideoConvertor)\] Destination: (.+)$'
    r'|^\[Merger\] Merging formats into "(.+)"$'
)
ERROR_PREFIX_RE = re.compile(r"^ERROR:\s*(?:\[[^\]]+\]\s*)?(?:[\w-]+:\s+)?")


def _na(value):
    return "" if value in ("NA", "None", "") else value


def parse_line(line):
    """Classify one line of yt-dlp output into (kind, data), or None.

    kinds: progress (dict), postprocess (name, status), file (final path),
    dest (intermediate path), error (message), warning (line)
    """
    line = line.strip()
    if line.startswith(DL_MARK):
        values = [v.strip() for v in line[len(DL_MARK):].split(SEP)]
        if len(values) != len(PROGRESS_FIELDS):
            return None
        status, size, percent, speed, eta, title, item, count = values
        return "progress", {
            "status": status,
            "size": _na(size),
            "percent": _na(percent),
            "speed": _na(speed),
            "eta": _na(eta),
            "title": _na(title),
            "item": int(item) if item.isdigit() else None,
            "count": int(count) if count.isdigit() else None,
        }
    if line.startswith(PP_MARK):
        name, _, status = line[len(PP_MARK):].partition(SEP)
        return "postprocess", (name, status)
    if m := MOVED_RE.match(line):
        return "file", m.group(1)
    if m := ALREADY_RE.match(line):
        return "file", m.group(1)
    if m := DEST_RE.match(line):
        return "dest", m.group(1) or m.group(2)
    if line.startswith("ERROR:"):
        return "error", ERROR_PREFIX_RE.sub("", line) or line
    if line.startswith("WARNING:"):
        return "warning", line
    return None


def run_process(command, on_line, on_start=None):
    """Run a command, passing each output line to on_line. on_start(proc) receives the
    process so the caller can terminate it. Returns the return code."""
    with sp.Popen(
        command,
        stdout=sp.PIPE,
        stderr=sp.STDOUT,
        stdin=sp.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        creationflags=CREATE_NO_WINDOW,
    ) as p:
        if on_start:
            on_start(p)
        for line in p.stdout:
            on_line(line.rstrip("\r\n"))
    return p.returncode


# ---- probing ----------------------------------------------------------------------


def probe(config, url, timeout=120):
    """Fetch title / duration / available formats / playlist entries without downloading.

    Returns a summary dict; raises RuntimeError with yt-dlp's message on failure.
    """
    p = sp.run(
        probe_command(config, url),
        capture_output=True,
        stdin=sp.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        creationflags=CREATE_NO_WINDOW,
        timeout=timeout,
    )
    if p.returncode != 0 or not p.stdout.strip():
        errors = [l for l in p.stderr.splitlines() if l.startswith("ERROR:")]
        message = ERROR_PREFIX_RE.sub("", errors[-1]) if errors else p.stderr.strip()[-300:]
        raise RuntimeError(message or f"yt-dlp exited with {p.returncode}")
    return summarize_info(json.loads(p.stdout))


def summarize_info(info):
    if info.get("_type") in ("playlist", "multi_video"):
        entries = [
            {
                "index": i,
                "title": e.get("title") or e.get("id") or f"Item {i}",
                "duration": e.get("duration"),
            }
            for i, e in enumerate(info.get("entries") or [], 1)
            if e
        ]
        return {
            "playlist": True,
            "title": info.get("title") or info.get("id") or "",
            "uploader": info.get("uploader") or info.get("channel") or "",
            "entries": entries,
            "formats": [],
            "duration": sum(e["duration"] or 0 for e in entries) or None,
        }
    formats = sorted(
        {
            (f["height"], round(f.get("fps") or 0))
            for f in info.get("formats") or []
            if f.get("vcodec") not in (None, "none") and f.get("height")
        },
        reverse=True,
    )
    return {
        "playlist": False,
        "title": info.get("track") or info.get("title") or "",
        "uploader": info.get("artist") or info.get("uploader") or "",
        "entries": [],
        "formats": [list(f) for f in formats],
        "duration": info.get("duration"),
    }


def expected_quality(formats, options):
    """Predict the resolution / fps the format sort will pick, e.g. "720p30"."""
    if not formats:
        return ""
    limit_h = int(options["resolution"]) if options["resolution"] != "best" else None
    limit_f = int(options["fps"]) if options["fps"] != "any" else None
    heights = sorted({h for h, _ in formats}, reverse=True)
    fitting = [h for h in heights if limit_h is None or h <= limit_h]
    height = fitting[0] if fitting else heights[-1]
    rates = sorted({f for h, f in formats if h == height}, reverse=True)
    fitting = [f for f in rates if limit_f is None or f <= limit_f]
    fps = fitting[0] if fitting else rates[-1]
    return f"{height}p{fps}" if fps else f"{height}p"


def max_quality(formats):
    return f"{formats[0][0]}p{formats[0][1] or ''}" if formats else ""


def format_duration(seconds):
    if not seconds:
        return ""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


# ---- OS helpers -------------------------------------------------------------------

URL_RE = re.compile(r"^https?://\S+$")


def find_urls(text):
    return [w for w in text.split() if URL_RE.match(w)]


def open_path(path):
    """Open a file or folder with the default application."""
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        sp.Popen(["open", path])
    else:
        sp.Popen(["xdg-open", path])


def reveal_path(path):
    """Show a file selected in the file manager (or open its folder)."""
    path = Path(path)
    if sys.platform == "win32" and path.is_file():
        sp.Popen(["explorer", "/select,", str(path)])
    elif sys.platform == "darwin" and path.exists():
        sp.Popen(["open", "-R", str(path)])
    else:
        open_path(str(path if path.is_dir() else path.parent))


def read_clipboard():
    """Plain-text clipboard contents, or "" (used by the TUI's clipboard watcher)."""
    try:
        if sys.platform == "win32":
            return _read_clipboard_windows()
        if sys.platform == "darwin":
            cmd = ["pbpaste"]
        elif os.environ.get("WAYLAND_DISPLAY"):
            cmd = ["wl-paste", "--no-newline"]
        else:
            cmd = ["xclip", "-selection", "clipboard", "-o"]
        return sp.run(cmd, capture_output=True, text=True, timeout=2).stdout
    except Exception:
        return ""


def _read_clipboard_windows():
    import ctypes
    from ctypes import wintypes

    CF_UNICODETEXT = 13
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    if not user32.OpenClipboard(None):
        return ""
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(pointer) if pointer else ""
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()
