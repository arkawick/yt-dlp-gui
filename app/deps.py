"""Download missing binaries (yt-dlp, ffmpeg, ffprobe, deno) and self-update yt-dlp.

Front-end agnostic: progress is reported through a plain callback.
"""

import logging
import os
import platform
import shutil
import stat
import subprocess as sp
import tarfile
import tempfile
import zipfile

import requests

from core import BIN_DIR, CREATE_NO_WINDOW, ROOT

logger = logging.getLogger(__name__)

os.environ["PATH"] += os.pathsep + str(BIN_DIR)
os.environ["PATH"] += os.pathsep + str(ROOT / "bin")  # old version compatibility

YT_DLP = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/"
FFMPEG = "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/"
FFMPEG_MAC = "https://ffmpeg.martin-riedl.de/redirect/latest/macos/"
DENO = "https://github.com/denoland/deno/releases/latest/download/"


def _arch():
    machine = platform.machine().lower()
    return "arm64" if machine in ("arm64", "aarch64") else "x64"


def sources():
    """{tool: [(url, {file in download: tool it provides})]} for this platform.

    ffmpeg and ffprobe come from one archive on Windows / Linux (yt-dlp's own builds,
    patched for yt-dlp) and from separate zips on macOS.
    """
    system, arch = platform.system(), _arch()
    if system == "Windows":
        ffmpeg = [(FFMPEG + "ffmpeg-master-latest-win64-gpl.zip",
                   {"ffmpeg.exe": "ffmpeg", "ffprobe.exe": "ffprobe"})]
        return {
            "yt-dlp": [(YT_DLP + "yt-dlp.exe", {"yt-dlp.exe": "yt-dlp"})],
            "ffmpeg": ffmpeg,
            "ffprobe": ffmpeg,
            "deno": [(DENO + "deno-x86_64-pc-windows-msvc.zip", {"deno.exe": "deno"})],
        }
    if system == "Linux":
        build = "linuxarm64" if arch == "arm64" else "linux64"
        ffmpeg = [(FFMPEG + f"ffmpeg-master-latest-{build}-gpl.tar.xz",
                   {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"})]
        triple = "aarch64" if arch == "arm64" else "x86_64"
        ytdlp = "yt-dlp_linux_aarch64" if arch == "arm64" else "yt-dlp_linux"
        return {
            "yt-dlp": [(YT_DLP + ytdlp, {ytdlp: "yt-dlp"})],
            "ffmpeg": ffmpeg,
            "ffprobe": ffmpeg,
            "deno": [(DENO + f"deno-{triple}-unknown-linux-gnu.zip", {"deno": "deno"})],
        }
    if system == "Darwin":
        mac = "arm64" if arch == "arm64" else "amd64"
        triple = "aarch64" if arch == "arm64" else "x86_64"
        return {
            "yt-dlp": [(YT_DLP + "yt-dlp_macos", {"yt-dlp_macos": "yt-dlp"})],
            "ffmpeg": [(FFMPEG_MAC + f"{mac}/release/ffmpeg.zip", {"ffmpeg": "ffmpeg"})],
            "ffprobe": [(FFMPEG_MAC + f"{mac}/release/ffprobe.zip", {"ffprobe": "ffprobe"})],
            "deno": [(DENO + f"deno-{triple}-apple-darwin.zip", {"deno": "deno"})],
        }
    return {}


def exe_name(tool):
    return tool + (".exe" if platform.system() == "Windows" else "")


def find_missing():
    """Tools that are neither on PATH nor in BIN_DIR."""
    return [
        tool for tool in sources()
        if not shutil.which(tool) and not (BIN_DIR / exe_name(tool)).exists()
    ]


def ensure_dependencies(update_ytdlp, progress):
    """Fetch missing binaries, then optionally run `yt-dlp -U`. progress(str) gets status text."""
    missing = find_missing()
    if missing:
        BIN_DIR.mkdir(parents=True, exist_ok=True)
        done = set()
        for tool in missing:
            for url, members in sources()[tool]:
                if url in done:  # ffmpeg + ffprobe share one archive
                    continue
                done.add(url)
                fetch(url, members, progress)

    if update_ytdlp and "yt-dlp" not in missing:
        update(progress)


def update(progress):
    output = ""
    updated = False

    with sp.Popen(
        ["yt-dlp", "-U"],
        stdout=sp.PIPE,
        stderr=sp.STDOUT,
        stdin=sp.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    ) as p:
        for line in p.stdout:
            output += line
            if line.startswith("Updating to"):
                updated = True
                progress("Updating yt-dlp...")

    if p.returncode != 0:
        logger.error(f"yt-dlp update failed returncode: {p.returncode}\n{output}")
    elif updated:
        progress("yt-dlp updated")
        logger.info(f"yt-dlp updated\n{output}")


def fetch(url, members, progress):
    """Download url and install the wanted files into BIN_DIR.

    members maps a file name (inside the archive, or the download itself) to a tool.
    """
    name = url.rsplit("/", 1)[-1]
    with download(url, progress) as data:
        if name.endswith(".zip"):
            with zipfile.ZipFile(data) as zf:
                files = {os.path.basename(n): n for n in zf.namelist() if not n.endswith("/")}
                for member, tool in members.items():
                    with zf.open(files[member]) as source:
                        install(source, tool)
        elif name.endswith((".tar.xz", ".tar.gz")):
            with tarfile.open(fileobj=data) as tf:
                files = {os.path.basename(m.name): m for m in tf.getmembers() if m.isfile()}
                for member, tool in members.items():
                    install(tf.extractfile(files[member]), tool)
        else:
            (tool,) = members.values()
            install(data, tool)


def install(source, tool):
    """Copy a file object to BIN_DIR as an executable tool."""
    target = BIN_DIR / exe_name(tool)
    temp = target.with_name(target.name + ".part")
    with open(temp, "wb") as out:
        shutil.copyfileobj(source, out, 1 << 20)
    temp.chmod(temp.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    temp.replace(target)
    logger.info(f"Installed {target}")


def download(url, progress):
    """Download into a temporary file (deleted on close), returned rewound."""
    response = requests.get(url, stream=True, timeout=30)
    response.raise_for_status()

    total = int(response.headers.get("content-length", 0))
    name = url.rsplit("/", 1)[-1]
    buffer = tempfile.TemporaryFile()
    last = -1
    for chunk in response.iter_content(chunk_size=1 << 16):
        buffer.write(chunk)
        done_mb = buffer.tell() / (1024 * 1024)
        if total:
            percent = buffer.tell() * 100 // total
            if percent != last:
                last = percent
                progress(f"Downloading dependency: ({name}) {done_mb:.1f} / "
                         f"{total / (1024 * 1024):.1f} MB {percent}%")
        else:
            progress(f"Downloading dependency: ({name}) {done_mb:.1f} MB")
    buffer.seek(0)
    return buffer
